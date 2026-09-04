/// Layer A: on-device vision screening.
///
/// Two implementations behind one interface:
///
/// * [TfliteVisionModel] — runs the real MobileNetV3-Small dual-head model from
///   `assets/models/satva_screen.tflite`. Used whenever the asset is present.
/// * [DevHeuristicScreener] — computes the morphological indicators the
///   specification names (colour uniformity, calyx-green versus body colour,
///   specular sheen, speckle density) directly from the pixels. Real image
///   analysis, but **not** a trained model.
///
/// The distinction is carried all the way through: [VisionResult.modelKind] is
/// stored on the scan, returned by the API, and rendered in the UI. Nothing in
/// SATVA presents a heuristic score as a model score, and nothing presents a
/// development-model score as a validated one.
///
/// **Neither implementation detects a chemical.** Layer A exists solely to
/// decide whether a ₹5 strip test is worth performing (specification §3.1).
library;

import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/foundation.dart';
import 'package:image/image.dart' as img;

/// Advisory verdict bands. Deliberately three, not two: a model that is unsure
/// should say so. Collapsing uncertainty into "not suspicious" hides risk, and
/// collapsing it into "suspicious" sends people to buy strips they do not need.
enum ScreeningVerdict {
  notSuspicious('not_suspicious'),
  inconclusive('inconclusive'),
  suspicious('suspicious'),
  refusedUnsupportedCrop('refused_unsupported_crop'),
  refusedCaptureQuality('refused_capture_quality');

  const ScreeningVerdict(this.wire);
  final String wire;
}

/// A region the screening attended to, in normalised image coordinates.
class SaliencyRegion {
  const SaliencyRegion({
    required this.x,
    required this.y,
    required this.width,
    required this.height,
    required this.weight,
    this.label,
  });

  final double x;
  final double y;
  final double width;
  final double height;
  final double weight;
  final String? label;

  Map<String, dynamic> toJson() => <String, dynamic>{
        'x': x,
        'y': y,
        'width': width,
        'height': height,
        'weight': weight,
        'label': label,
      };
}

class VisionResult {
  const VisionResult({
    required this.anomalyScore,
    required this.verdict,
    required this.modelId,
    required this.modelKind,
    required this.isValidatedModel,
    this.ripenessIndex,
    this.inferenceMs,
    this.embedding,
    this.saliency = const <SaliencyRegion>[],
    this.refusalReason,
  });

  /// Calibrated 0–100. Advisory only, never evidence.
  final double anomalyScore;
  final ScreeningVerdict verdict;
  final String modelId;

  /// 'tflite_float16' | 'tflite_int8' | 'dev_heuristic'
  final String modelKind;

  /// False for everything shipped in this build. Drives the UI warning.
  final bool isValidatedModel;

  final double? ripenessIndex;
  final int? inferenceMs;
  final Float32List? embedding;
  final List<SaliencyRegion> saliency;
  final String? refusalReason;

  bool get suggestsStripTest =>
      verdict == ScreeningVerdict.suspicious ||
      verdict == ScreeningVerdict.inconclusive;

  Map<String, dynamic> toApiPayload() => <String, dynamic>{
        'anomaly_score': anomalyScore,
        'verdict': verdict.wire,
        'model_id': modelId,
        'model_kind': modelKind,
        'inference_ms': inferenceMs,
        'ripeness_index': ripenessIndex,
        'saliency': saliency.map((SaliencyRegion r) => r.toJson()).toList(),
      };
}

/// Band boundaries on the 0–100 score. Triage boundaries, not diagnostic
/// thresholds: the only decision they drive is whether to suggest a strip test.
class ScreeningBands {
  const ScreeningBands({
    this.notSuspiciousBelow = 35.0,
    this.suspiciousAtOrAbove = 60.0,
  });

  final double notSuspiciousBelow;
  final double suspiciousAtOrAbove;

  ScreeningVerdict classify(double score) {
    if (score >= suspiciousAtOrAbove) return ScreeningVerdict.suspicious;
    if (score < notSuspiciousBelow) return ScreeningVerdict.notSuspicious;
    return ScreeningVerdict.inconclusive;
  }
}

const ScreeningBands defaultBands = ScreeningBands();

abstract class VisionScreener {
  String get modelId;
  String get modelKind;
  bool get isValidatedModel;

  Future<void> load();
  Future<VisionResult> screen(img.Image image, {required String crop});
  void dispose();
}

/// Morphological screening without a trained model.
///
/// This is genuine image analysis, not a random number. It computes the four
/// indicators the specification lists in §3.1, each on a 0–1 scale, and combines
/// them into a 0–100 score:
///
/// 1. **Colour uniformity** — forced ripening colours skin unnaturally evenly,
///    so *low* hue variance across the produce body is suspicious.
/// 2. **Calyx divergence** — the stem end stays green while the body colours.
///    A large hue gap between the top region and the body is suspicious.
/// 3. **Specular sheen** — waxed or forced-ripened skin gives a tight, bright
///    highlight rather than a broad soft one.
/// 4. **Speckle density** — small dark lesions where carbide contacted skin.
///
/// It is used when no trained model asset is present, and it is labelled
/// `dev_heuristic` everywhere the result travels. It must never be described as
/// a model, and its output must never be quoted as an accuracy figure.
class DevHeuristicScreener implements VisionScreener {
  @override
  String get modelId => 'satva-heuristic-0.1';

  @override
  String get modelKind => 'dev_heuristic';

  @override
  bool get isValidatedModel => false;

  @override
  Future<void> load() async {}

  @override
  void dispose() {}

  @override
  Future<VisionResult> screen(img.Image image, {required String crop}) async {
    final Stopwatch stopwatch = Stopwatch()..start();
    final img.Image small = img.copyResize(image, width: 192);

    final _Indicators indicators = _computeIndicators(small);

    // Weighted combination. Colour uniformity and calyx divergence carry most
    // weight because they are the signatures the specification calls out first
    // and are the most robust to lighting.
    final double raw = 0.34 * indicators.colourUniformity +
        0.30 * indicators.calyxDivergence +
        0.18 * indicators.speckleDensity +
        0.18 * indicators.specularSheen;

    final double score = (raw * 100).clamp(0.0, 100.0).toDouble();

    return VisionResult(
      anomalyScore: double.parse(score.toStringAsFixed(1)),
      verdict: defaultBands.classify(score),
      modelId: modelId,
      modelKind: modelKind,
      isValidatedModel: false,
      ripenessIndex: indicators.ripeness,
      inferenceMs: stopwatch.elapsedMilliseconds,
      saliency: indicators.regions,
    );
  }

  _Indicators _computeIndicators(img.Image image) {
    final int width = image.width;
    final int height = image.height;

    // Rough foreground mask: the produce is assumed to be the central, more
    // saturated region. A segmentation model would do better; this is adequate
    // for a fallback and costs nothing.
    final List<double> hues = <double>[];
    final List<double> values = <double>[];
    int brightPixels = 0;
    int darkSpots = 0;
    int foreground = 0;

    double topHueSum = 0;
    int topCount = 0;
    double bodyHueSum = 0;
    int bodyCount = 0;

    for (int y = 0; y < height; y++) {
      for (int x = 0; x < width; x++) {
        final img.Pixel pixel = image.getPixel(x, y);
        final double r = pixel.r / 255.0;
        final double g = pixel.g / 255.0;
        final double b = pixel.b / 255.0;

        final double maxC = math.max(r, math.max(g, b));
        final double minC = math.min(r, math.min(g, b));
        final double delta = maxC - minC;
        final double saturation = maxC <= 0 ? 0 : delta / maxC;

        // Central-ellipse foreground test.
        final double nx = (x - width / 2) / (width * 0.42);
        final double ny = (y - height / 2) / (height * 0.42);
        final bool inCentre = nx * nx + ny * ny <= 1.0;
        if (!inCentre || saturation < 0.12) continue;

        foreground++;
        double hue;
        if (delta < 1e-6) {
          hue = 0;
        } else if (maxC == r) {
          hue = 60 * (((g - b) / delta) % 6);
        } else if (maxC == g) {
          hue = 60 * (((b - r) / delta) + 2);
        } else {
          hue = 60 * (((r - g) / delta) + 4);
        }
        if (hue < 0) hue += 360;

        hues.add(hue);
        values.add(maxC);

        if (maxC > 0.93 && saturation < 0.25) brightPixels++;
        if (maxC < 0.28) darkSpots++;

        if (y < height * 0.28) {
          topHueSum += hue;
          topCount++;
        } else if (y > height * 0.40) {
          bodyHueSum += hue;
          bodyCount++;
        }
      }
    }

    if (foreground < 200 || hues.isEmpty) {
      return const _Indicators(0.2, 0.2, 0.1, 0.1, 0.5, <SaliencyRegion>[]);
    }

    // 1. Colour uniformity. Circular standard deviation of hue: low spread
    // means unnaturally even colouring.
    double sinSum = 0, cosSum = 0;
    for (final double hue in hues) {
      final double radians = hue * math.pi / 180.0;
      sinSum += math.sin(radians);
      cosSum += math.cos(radians);
    }
    final double resultant =
        math.sqrt(sinSum * sinSum + cosSum * cosSum) / hues.length;
    // resultant near 1 means tightly clustered hue.
    final double colourUniformity = resultant.clamp(0.0, 1.0).toDouble();

    // 2. Calyx divergence: circular hue gap between the top band and the body.
    double calyxDivergence = 0.0;
    if (topCount > 30 && bodyCount > 30) {
      final double topHue = topHueSum / topCount;
      final double bodyHue = bodyHueSum / bodyCount;
      double gap = (topHue - bodyHue).abs();
      if (gap > 180) gap = 360 - gap;
      // A 60-degree gap (green stem against orange body) reads as maximal.
      calyxDivergence = (gap / 60.0).clamp(0.0, 1.0).toDouble();
    }

    // 3. Specular sheen: fraction of near-white low-saturation pixels.
    final double specularSheen =
        (brightPixels / foreground * 12.0).clamp(0.0, 1.0).toDouble();

    // 4. Speckle density: fraction of dark lesion pixels.
    final double speckleDensity =
        (darkSpots / foreground * 8.0).clamp(0.0, 1.0).toDouble();

    // Ripeness proxy: mean value, warmer and brighter reads as riper.
    final double meanValue =
        values.reduce((double a, double b) => a + b) / values.length;
    final double ripeness = meanValue.clamp(0.0, 1.0).toDouble();

    final List<SaliencyRegion> regions = <SaliencyRegion>[
      if (calyxDivergence > 0.35)
        SaliencyRegion(
          x: 0.30,
          y: 0.05,
          width: 0.40,
          height: 0.26,
          weight: calyxDivergence,
          label: 'stem end still green',
        ),
      if (colourUniformity > 0.7)
        SaliencyRegion(
          x: 0.22,
          y: 0.35,
          width: 0.56,
          height: 0.42,
          weight: colourUniformity,
          label: 'unusually even skin colour',
        ),
      if (speckleDensity > 0.3)
        SaliencyRegion(
          x: 0.30,
          y: 0.45,
          width: 0.42,
          height: 0.34,
          weight: speckleDensity,
          label: 'surface speckling',
        ),
      if (specularSheen > 0.4)
        SaliencyRegion(
          x: 0.28,
          y: 0.24,
          width: 0.26,
          height: 0.26,
          weight: specularSheen,
          label: 'unnatural sheen',
        ),
    ];

    return _Indicators(
      colourUniformity,
      calyxDivergence,
      speckleDensity,
      specularSheen,
      ripeness,
      regions,
    );
  }
}

class _Indicators {
  const _Indicators(
    this.colourUniformity,
    this.calyxDivergence,
    this.speckleDensity,
    this.specularSheen,
    this.ripeness,
    this.regions,
  );

  final double colourUniformity;
  final double calyxDivergence;
  final double speckleDensity;
  final double specularSheen;
  final double ripeness;
  final List<SaliencyRegion> regions;
}
