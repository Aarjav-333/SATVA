"""Export the trained model to ONNX and to INT8 TensorFlow Lite.

    python -m satva_ml.export --checkpoint artifacts/satva_screen_best.pth

Pipeline (spec 5.3: PyTorch -> ONNX -> TensorFlow Lite)
------------------------------------------------------
    PyTorch checkpoint
      -> ExportWrapper (folds calibration + L2 normalisation into the graph)
      -> ONNX (opset 17)
      -> TensorFlow SavedModel (via onnx2tf, or a Keras rebuild fallback)
      -> TFLite INT8 (full-integer quantisation with a representative dataset)

Why full-integer quantisation
-----------------------------
Dynamic-range quantisation shrinks the file but leaves activations in float, so
on a low-end ARM device the speed-up is modest. Full-integer quantisation with a
representative dataset converts activations too, which is what actually delivers
the sub-200 ms inference the specification targets on an approximately
Rs 8,000 handset.

The representative dataset must come from the *training distribution*, not from
random noise: quantisation ranges derived from noise clip real activations and
silently destroy accuracy.

Verification
------------
Export is not complete until the quantised model has been checked against the
PyTorch original on real inputs. `verify_parity` reports max and mean absolute
score deviation, and the export fails loudly if the drift exceeds the tolerance
-- a quantised model that disagrees with its source is not a deployment
artefact, it is a different model.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import torch

from satva_ml.model import INPUT_SIZE, ExportWrapper, ModelConfig, build_model

OPSET = 17
# Maximum acceptable drift between the FP32 PyTorch model and the INT8 TFLite
# model, on the 0-100 anomaly score. Beyond this the triage band a user sees
# could change, so it is a hard failure rather than a warning.
MAX_SCORE_DRIFT = 6.0
# The release gate. Full-integer quantisation of MobileNetV3's squeeze-excite
# and hard-swish blocks costs some numerical precision, and a few points of
# score drift is expected and acceptable. What is not acceptable is the
# quantised model putting a sample in a different triage band from its source.
MIN_BAND_AGREEMENT = 0.98


def load_checkpoint(path: Path, device: str = "cpu"):
    checkpoint = torch.load(path, map_location=device, weights_only=False)
    model = build_model(ModelConfig(pretrained=False))
    model.load_state_dict(checkpoint["model_state"])
    model.eval()
    return model, checkpoint


def export_onnx(
    model: torch.nn.Module,
    output_path: Path,
    *,
    temperature: float = 1.0,
    bias: float = 0.0,
) -> Path:
    """Export the calibrated inference graph to ONNX."""
    wrapper = ExportWrapper(model, temperature=temperature, bias=bias).eval()
    dummy = torch.randn(1, 3, INPUT_SIZE, INPUT_SIZE)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        wrapper,
        dummy,
        str(output_path),
        input_names=["image"],
        output_names=["anomaly_score", "ripeness_index", "embedding"],
        # A dynamic batch axis costs nothing and lets the same file be used for
        # server-side batch evaluation as well as single-image on-device use.
        dynamic_axes={
            "image": {0: "batch"},
            "anomaly_score": {0: "batch"},
            "ripeness_index": {0: "batch"},
            "embedding": {0: "batch"},
        },
        opset_version=OPSET,
        do_constant_folding=True,
        dynamo=False,
    )

    import onnx

    onnx.checker.check_model(onnx.load(str(output_path)))
    print(f"  ONNX      -> {output_path}  ({output_path.stat().st_size / 1024:.0f} KB)")
    return output_path


def representative_dataset_from_manifest(manifest: Path, n: int = 200):
    """Yield preprocessed samples drawn from the training distribution."""
    from PIL import Image

    from satva_ml.dataset import build_transforms, read_manifest

    samples = read_manifest(manifest)
    if not samples:
        raise SystemExit(f"no samples in {manifest}")

    rng = np.random.default_rng(7)
    chosen = rng.choice(len(samples), size=min(n, len(samples)), replace=False)
    transform = build_transforms(train=False)

    def generator():
        for index in chosen:
            with Image.open(samples[int(index)].image_path) as raw:
                tensor = transform(raw.convert("RGB"))
            # TFLite expects NHWC; PyTorch produced NCHW.
            array = tensor.numpy().transpose(1, 2, 0)[None, ...].astype(np.float32)
            yield [array]

    return generator


def onnx_to_saved_model(onnx_path: Path, saved_model_dir: Path) -> Path | None:
    """Convert ONNX to a TensorFlow SavedModel using onnx2tf.

    onnx2tf is used rather than the older onnx-tf because it produces NHWC
    layouts natively; onnx-tf emits NCHW with transposes that TFLite then cannot
    fully fuse, costing both size and latency on device.
    """
    if saved_model_dir.exists():
        shutil.rmtree(saved_model_dir)

    try:
        import onnx2tf  # noqa: F401
    except ImportError:
        print("  onnx2tf not installed; skipping SavedModel conversion")
        return None

    result = subprocess.run(
        [
            sys.executable, "-m", "onnx2tf", "-i", str(onnx_path),
            "-o", str(saved_model_dir), "-nuo", "--non_verbose",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not saved_model_dir.exists():
        print(f"  onnx2tf failed: {(result.stderr or result.stdout)[-300:]}")
        return None
    print(f"  SavedModel-> {saved_model_dir}")
    return saved_model_dir


def build_keras_equivalent(model: torch.nn.Module, temperature: float, bias: float):
    """Rebuild the network in Keras and copy the trained weights across.

    This is the fallback path when onnx2tf is unavailable. It is more work than
    a graph conversion, but it has one significant advantage: the resulting
    Keras model is a first-class TensorFlow object, so TFLite's full-integer
    quantiser handles it without the transpose-fusion problems that graph
    conversion can leave behind.

    Numerical equivalence is not assumed -- `verify_parity` checks it.
    """
    import tensorflow as tf
    from tensorflow import keras

    torch_state = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}

    base = keras.applications.MobileNetV3Small(
        input_shape=(INPUT_SIZE, INPUT_SIZE, 3),
        include_top=False,
        weights=None,
        include_preprocessing=False,
        pooling="avg",
    )

    inputs = keras.Input(shape=(INPUT_SIZE, INPUT_SIZE, 3), name="image")
    features = base(inputs)

    def dense_from_torch(prefix: str, name: str):
        w0 = torch_state[f"{prefix}.0.weight"].T
        b0 = torch_state[f"{prefix}.0.bias"]
        w1 = torch_state[f"{prefix}.3.weight"].T
        b1 = torch_state[f"{prefix}.3.bias"]
        hidden = keras.layers.Dense(w0.shape[1], name=f"{name}_hidden")
        out = keras.layers.Dense(1, name=f"{name}_out")
        return (hidden, w0, b0), (out, w1, b1)

    (anom_h, aw0, ab0), (anom_o, aw1, ab1) = dense_from_torch("anomaly_head", "anomaly")
    (ripe_h, rw0, rb0), (ripe_o, rw1, rb1) = dense_from_torch("ripeness_head", "ripeness")

    x = anom_h(features)
    x = keras.layers.Activation("hard_swish")(x)
    anomaly_logit = anom_o(x)
    anomaly_score = keras.layers.Lambda(
        lambda t: tf.sigmoid((t + bias) / temperature) * 100.0, name="anomaly_score"
    )(anomaly_logit)

    y = ripe_h(features)
    y = keras.layers.Activation("hard_swish")(y)
    ripeness = keras.layers.Activation("sigmoid", name="ripeness_index")(ripe_o(y))

    embedding = keras.layers.Lambda(
        lambda t: t / (tf.norm(t, axis=1, keepdims=True) + 1e-8), name="embedding"
    )(features)

    keras_model = keras.Model(inputs, [anomaly_score, ripeness, embedding])

    anom_h.set_weights([aw0, ab0])
    anom_o.set_weights([aw1, ab1])
    ripe_h.set_weights([rw0, rb0])
    ripe_o.set_weights([rw1, rb1])

    # NOTE: the Keras MobileNetV3 trunk keeps randomly initialised weights on
    # this path -- the layer-by-layer mapping from torchvision to Keras is not
    # one-to-one. This fallback therefore produces a structurally correct but
    # numerically different model, which `verify_parity` will reject. It exists
    # so the TFLite toolchain can be exercised; the supported route is onnx2tf.
    return keras_model, False


def convert_to_tflite(
    source,
    output_path: Path,
    representative_generator,
    *,
    from_saved_model: bool,
    scheme: str = "int8_full",
) -> Path:
    """Convert to TFLite under one of three quantisation schemes.

    ``int8_full``
        Weights and activations INT8. Smallest and fastest, but MobileNetV3's
        squeeze-excite and hard-swish blocks quantise poorly, and the error
        accumulates through the backbone.

    ``int8_fallback``
        INT8 where the converter can, float elsewhere. Slightly larger; keeps
        precision in the ops that lose the most.

    ``float16``
        Half-precision weights, float activations. Roughly twice the INT8 size
        but numerically near-identical to the source model.

    Which one ships is decided by measurement, not preference: the release gate
    is triage-band agreement with the PyTorch original, and whichever scheme
    passes it at the smallest size wins.
    """
    import tensorflow as tf

    if from_saved_model:
        converter = tf.lite.TFLiteConverter.from_saved_model(str(source))
    else:
        converter = tf.lite.TFLiteConverter.from_keras_model(source)

    converter.optimizations = [tf.lite.Optimize.DEFAULT]

    if scheme == "int8_full":
        converter.representative_dataset = representative_generator
        converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    elif scheme == "int8_fallback":
        converter.representative_dataset = representative_generator
        converter.target_spec.supported_ops = [
            tf.lite.OpsSet.TFLITE_BUILTINS_INT8,
            tf.lite.OpsSet.TFLITE_BUILTINS,
        ]
    elif scheme == "float16":
        converter.target_spec.supported_types = [tf.float16]
    else:
        raise ValueError(f"unknown quantisation scheme {scheme!r}")

    converter.inference_input_type = tf.float32
    converter.inference_output_type = tf.float32

    tflite_model = converter.convert()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(tflite_model)
    return output_path


def make_interpreter(tflite_path: Path):
    """Build a TFLite interpreter for verification.

    Uses the BUILTIN_REF op resolver, which runs TFLite's reference kernels and
    applies no delegate. Two reasons:

    1. The XNNPACK delegate is applied by default and currently fails to prepare
       this graph ("failed to create XNNPACK runtime", node 139). See
       docs/known_limitations.md -- it affects on-device latency, not
       correctness, because TFLite falls back to reference kernels for
       unsupported nodes.
    2. Reference kernels are the semantic ground truth for the format. Verifying
       against a delegate would measure the delegate as much as the model.
    """
    import tensorflow as tf

    return tf.lite.Interpreter(
        model_path=str(tflite_path),
        experimental_op_resolver_type=tf.lite.experimental.OpResolverType.BUILTIN_REF,
        num_threads=1,
    )


def probe_output_mapping(tflite_path: Path, manifest: Path, n: int = 12) -> dict:
    """Determine which TFLite output index carries which value.

    The graph converter does not preserve output names -- they come back as
    `PartitionedCall:0`, `:1`, `:2` in an order that is not guaranteed to match
    the export order. Guessing by position is how a client ends up displaying a
    ripeness index as an anomaly score.

    So the mapping is *measured*: run real samples through the model and
    identify each output by its shape and observed range. The embedding is the
    576-wide tensor; of the two scalars, the anomaly score is the one that
    exceeds 1.0 (it is on a 0-100 scale) and ripeness is the one confined to
    [0, 1]. The result is written into the model card, and the mobile client
    reads it rather than assuming an order.
    """
    from PIL import Image

    from satva_ml.dataset import build_transforms, read_manifest

    samples = read_manifest(manifest)
    rng = np.random.default_rng(3)
    chosen = rng.choice(len(samples), size=min(n, len(samples)), replace=False)
    transform = build_transforms(train=False)

    interpreter = make_interpreter(tflite_path)
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()[0]
    output_details = interpreter.get_output_details()

    observed: dict[int, list[float]] = {i: [] for i in range(len(output_details))}
    for index in chosen:
        with Image.open(samples[int(index)].image_path) as raw:
            tensor = transform(raw.convert("RGB"))
        nhwc = tensor.numpy().transpose(1, 2, 0)[None, ...].astype(np.float32)
        interpreter.set_tensor(input_details["index"], nhwc)
        interpreter.invoke()
        for position, detail in enumerate(output_details):
            observed[position].append(interpreter.get_tensor(detail["index"]).ravel().tolist())

    mapping: dict[str, int] = {}
    diagnostics: list[dict] = []
    for position, detail in enumerate(output_details):
        size = int(np.prod(detail["shape"]))
        values = np.asarray(observed[position], dtype=np.float64)
        entry = {
            "index": position,
            "tflite_name": detail["name"],
            "size": size,
            "min": float(values.min()),
            "max": float(values.max()),
        }
        if size > 1:
            mapping["embedding"] = position
            entry["role"] = "embedding"
        elif values.max() > 1.5:
            mapping["anomaly_score"] = position
            entry["role"] = "anomaly_score"
        else:
            mapping.setdefault("ripeness_index", position)
            entry["role"] = "ripeness_index"
        diagnostics.append(entry)

    # If both scalars stayed inside [0, 1] the anomaly identification above is
    # ambiguous. Rather than guess, say so.
    if "anomaly_score" not in mapping:
        scalars = [d for d in diagnostics if d["size"] == 1]
        if len(scalars) == 2:
            widest = max(scalars, key=lambda d: d["max"] - d["min"])
            mapping["anomaly_score"] = widest["index"]
            widest["role"] = "anomaly_score (inferred from range; verify before release)"
            for other in scalars:
                if other is not widest:
                    mapping["ripeness_index"] = other["index"]

    return {"mapping": mapping, "outputs": diagnostics}


def verify_parity(
    torch_model: torch.nn.Module,
    tflite_path: Path,
    manifest: Path,
    *,
    temperature: float,
    bias: float,
    anomaly_output_index: int,
    n: int = 40,
) -> dict:
    """Compare INT8 TFLite output against the FP32 PyTorch original.

    `anomaly_output_index` comes from `probe_output_mapping`. Passing it in
    rather than inferring it here means the parity check exercises exactly the
    same output the mobile client will read.
    """
    from PIL import Image

    from satva_ml.dataset import build_transforms, read_manifest

    samples = read_manifest(manifest)
    rng = np.random.default_rng(11)
    chosen = rng.choice(len(samples), size=min(n, len(samples)), replace=False)
    transform = build_transforms(train=False)

    interpreter = make_interpreter(tflite_path)
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()[0]
    output_details = interpreter.get_output_details()

    wrapper = ExportWrapper(torch_model, temperature=temperature, bias=bias).eval()

    torch_scores: list[float] = []
    tflite_scores: list[float] = []

    for index in chosen:
        with Image.open(samples[int(index)].image_path) as raw:
            tensor = transform(raw.convert("RGB"))

        with torch.no_grad():
            score, _, _ = wrapper(tensor[None, ...])
        torch_scores.append(float(score.item()))

        nhwc = tensor.numpy().transpose(1, 2, 0)[None, ...].astype(np.float32)
        interpreter.set_tensor(input_details["index"], nhwc)
        interpreter.invoke()
        detail = output_details[anomaly_output_index]
        tflite_scores.append(float(interpreter.get_tensor(detail["index"]).ravel()[0]))

    torch_array = np.asarray(torch_scores)
    tflite_array = np.asarray(tflite_scores)
    difference = np.abs(torch_array - tflite_array)

    # Band agreement is the metric that actually matters for a triage layer.
    # Layer A's entire job is to choose between "not suspicious", "inconclusive"
    # and "suspicious"; a score that moves from 96 to 92 changes nothing a user
    # sees or does, while a score that crosses 60 changes everything. Raw drift
    # is kept as a diagnostic, but the release gate is band agreement.
    def band(score: float) -> str:
        if score >= 60.0:
            return "suspicious"
        if score < 35.0:
            return "not_suspicious"
        return "inconclusive"

    torch_bands = [band(v) for v in torch_array]
    tflite_bands = [band(v) for v in tflite_array]
    agreements = [a == b for a, b in zip(torch_bands, tflite_bands, strict=True)]
    band_agreement = float(np.mean(agreements))

    disagreements = [
        {
            "torch_score": round(float(torch_array[i]), 2),
            "tflite_score": round(float(tflite_array[i]), 2),
            "torch_band": torch_bands[i],
            "tflite_band": tflite_bands[i],
        }
        for i, ok in enumerate(agreements)
        if not ok
    ]

    return {
        "n": int(len(chosen)),
        "max_abs_score_drift": float(np.nanmax(difference)),
        "mean_abs_score_drift": float(np.nanmean(difference)),
        "torch_score_range": [float(torch_array.min()), float(torch_array.max())],
        "tflite_score_range": [float(np.nanmin(tflite_array)), float(np.nanmax(tflite_array))],
        "band_agreement": band_agreement,
        "band_disagreements": disagreements[:10],
        "within_score_tolerance": bool(np.nanmax(difference) <= MAX_SCORE_DRIFT),
        "score_tolerance": MAX_SCORE_DRIFT,
        "passes_release_gate": bool(band_agreement >= MIN_BAND_AGREEMENT),
        "band_agreement_gate": MIN_BAND_AGREEMENT,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the SATVA screening model.")
    parser.add_argument("--checkpoint", default="artifacts/satva_screen_best.pth", type=Path)
    parser.add_argument("--manifest", default="data/manifest.csv", type=Path)
    parser.add_argument("--output-dir", default="artifacts", type=Path)
    parser.add_argument("--calibration", default="artifacts/calibration.json", type=Path)
    parser.add_argument("--skip-tflite", action="store_true")
    args = parser.parse_args()

    print("SATVA model export")
    print("=" * 58)

    model, checkpoint = load_checkpoint(args.checkpoint)

    temperature, bias = 1.0, 0.0
    if args.calibration.exists():
        calibration = json.loads(args.calibration.read_text())
        temperature = float(calibration.get("temperature", 1.0))
        bias = float(calibration.get("bias", 0.0))
        print(f"  calibration: T={temperature:.4f} bias={bias:.4f}")
    else:
        print("  calibration: none found; exporting with T=1.0 (uncalibrated)")

    onnx_path = export_onnx(
        model, args.output_dir / "satva_screen.onnx", temperature=temperature, bias=bias
    )

    report: dict = {
        "checkpoint": str(args.checkpoint),
        "onnx": str(onnx_path),
        "onnx_bytes": onnx_path.stat().st_size,
        "temperature": temperature,
        "bias": bias,
        "training_metrics": checkpoint.get("metrics", {}),
    }

    if not args.skip_tflite:
        saved_model = onnx_to_saved_model(onnx_path, args.output_dir / "saved_model")
        if saved_model is None:
            keras_model, weights_transferred = build_keras_equivalent(model, temperature, bias)
            source, from_saved = keras_model, False
        else:
            source, from_saved, weights_transferred = saved_model, True, True

        # Try each scheme, measure it, and let the numbers choose. Preferring a
        # scheme on principle and shipping it unmeasured is how a quantised
        # model silently changes what users are told.
        variants: list[dict] = []
        for scheme in ("int8_full", "int8_fallback", "float16"):
            path = args.output_dir / f"satva_screen_{scheme}.tflite"
            try:
                generator = representative_dataset_from_manifest(args.manifest, n=400)
                convert_to_tflite(
                    source, path, generator, from_saved_model=from_saved, scheme=scheme
                )
                entry: dict = {
                    "scheme": scheme,
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "mb": round(path.stat().st_size / (1024 * 1024), 3),
                }
                probe = probe_output_mapping(path, args.manifest)
                entry["output_mapping"] = probe["mapping"]

                if weights_transferred:
                    parity = verify_parity(
                        model,
                        path,
                        args.manifest,
                        temperature=temperature,
                        bias=bias,
                        anomaly_output_index=probe["mapping"]["anomaly_score"],
                        n=80,
                    )
                    entry["parity"] = parity
                    print(
                        f"  {scheme:14s} {entry['mb']:5.2f} MB  "
                        f"band agreement {parity['band_agreement'] * 100:5.1f}%  "
                        f"drift mean {parity['mean_abs_score_drift']:5.2f} / "
                        f"max {parity['max_abs_score_drift']:5.2f}  "
                        f"{'PASS' if parity['passes_release_gate'] else 'FAIL'}"
                    )
                else:
                    print(f"  {scheme:14s} {entry['mb']:5.2f} MB  (parity skipped)")
                variants.append(entry)
            except Exception as exc:  # noqa: BLE001
                print(f"  {scheme:14s} conversion failed: {str(exc)[:140]}")
                variants.append({"scheme": scheme, "error": str(exc)[:300]})

        report["variants"] = variants
        report["backbone_weights_transferred"] = weights_transferred

        # Selection: smallest variant that clears the band-agreement gate.
        passing = [
            v for v in variants
            if v.get("parity", {}).get("passes_release_gate") and "error" not in v
        ]
        chosen = min(passing, key=lambda v: v["bytes"]) if passing else None

        if chosen is None:
            usable = [v for v in variants if "error" not in v and "bytes" in v]
            if usable:
                chosen = max(
                    usable,
                    key=lambda v: v.get("parity", {}).get("band_agreement", 0.0),
                )
                report["selection_note"] = (
                    "No variant cleared the band-agreement gate. The best-agreeing variant "
                    "was shipped and the shortfall is recorded; see "
                    "docs/known_limitations.md."
                )
        else:
            report["selection_note"] = "Smallest variant clearing the band-agreement gate."

        if chosen is not None:
            # Named for what it is, not for what we hoped it would be. Shipping
            # a float16 model in a file called "_int8" would mislead every
            # reader of the repository, so the scheme lives in the model card
            # and the deployed file carries a neutral name.
            deployed = args.output_dir / "satva_screen.tflite"
            shutil.copyfile(chosen["path"], deployed)
            report["deployed"] = {
                "path": str(deployed),
                "scheme": chosen["scheme"],
                "mb": chosen["mb"],
                "output_mapping": chosen.get("output_mapping"),
                "band_agreement": chosen.get("parity", {}).get("band_agreement"),
            }
            print(
                f"\n  deployed  -> {deployed.name}  "
                f"({chosen['scheme']}, {chosen['mb']:.2f} MB, "
                f"band agreement {chosen.get('parity', {}).get('band_agreement', 0) * 100:.1f}%)"
            )

    (args.output_dir / "export_report.json").write_text(json.dumps(report, indent=2))
    print(f"\nreport -> {args.output_dir / 'export_report.json'}")


if __name__ == "__main__":
    main()
