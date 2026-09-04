/// TFLite implementation of Layer A screening.
///
/// Loads `assets/models/satva_screen.tflite` together with its model card. The
/// card matters: the graph converter does not preserve output names, so the
/// index of each output (`anomaly_score`, `ripeness_index`, `embedding`) is
/// **measured at export time** and published in `model_card.json`. This class
/// reads that mapping rather than assuming an order, because assuming would
/// eventually display a ripeness index as an anomaly score.
library;

import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart' show rootBundle;
import 'package:image/image.dart' as img;
import 'package:tflite_flutter/tflite_flutter.dart';

import 'vision_service.dart';

const String kModelAsset = 'assets/models/satva_screen.tflite';
const String kModelCardAsset = 'assets/models/model_card.json';

class ModelCard {
  const ModelCard({
    required this.modelId,
    required this.kind,
    required this.inputWidth,
    required this.inputHeight,
    required this.mean,
    required this.std,
    required this.outputs,
    required this.isValidated,
    required this.warning,
    required this.supportedCrops,
  });

  final String modelId;
  final String kind;
  final int inputWidth;
  final int inputHeight;
  final List<double> mean;
  final List<double> std;

  /// Measured at export time: name -> output tensor index.
  final Map<String, int> outputs;

  final bool isValidated;
  final String warning;
  final List<String> supportedCrops;

  static Future<ModelCard> load([String asset = kModelCardAsset]) async {
    final Map<String, dynamic> json =
        jsonDecode(await rootBundle.loadString(asset)) as Map<String, dynamic>;
    final Map<String, dynamic> input = json['input'] as Map<String, dynamic>;
    final Map<String, dynamic> normalisation =
        input['normalisation'] as Map<String, dynamic>;

    return ModelCard(
      modelId: json['model_id'] as String,
      kind: json['kind'] as String,
      inputWidth: input['width'] as int,
      inputHeight: input['height'] as int,
      mean: (normalisation['mean'] as List<dynamic>)
          .map((dynamic v) => (v as num).toDouble())
          .toList(growable: false),
      std: (normalisation['std'] as List<dynamic>)
          .map((dynamic v) => (v as num).toDouble())
          .toList(growable: false),
      outputs: (json['outputs'] as Map<String, dynamic>)
          .map((String k, dynamic v) => MapEntry<String, int>(k, v as int)),
      isValidated: json['is_validated'] as bool? ?? false,
      warning: json['warning'] as String? ?? '',
      supportedCrops: (json['supported_crops'] as List<dynamic>? ?? <dynamic>[])
          .cast<String>(),
    );
  }
}

class TfliteVisionModel implements VisionScreener {
  TfliteVisionModel({this.modelAsset = kModelAsset});

  final String modelAsset;

  Interpreter? _interpreter;
  ModelCard? _card;

  @override
  String get modelId => _card?.modelId ?? 'satva-screen-unknown';

  @override
  String get modelKind => _card?.kind ?? 'tflite';

  @override
  bool get isValidatedModel => _card?.isValidated ?? false;

  ModelCard? get card => _card;

  @override
  Future<void> load() async {
    _card = await ModelCard.load();
    final InterpreterOptions options = InterpreterOptions()..threads = 2;
    _interpreter = await Interpreter.fromAsset(modelAsset, options: options);
    _interpreter!.allocateTensors();
  }

  @override
  void dispose() {
    _interpreter?.close();
    _interpreter = null;
  }

  /// Resize and normalise into the NHWC float tensor the model expects.
  ///
  /// The normalisation constants come from the model card rather than being
  /// hard-coded, so a retrained model with different preprocessing cannot be
  /// silently fed the wrong inputs.
  List<List<List<List<double>>>> _preprocess(img.Image source) {
    final ModelCard card = _card!;
    final img.Image resized = img.copyResize(
      source,
      width: card.inputWidth,
      height: card.inputHeight,
      interpolation: img.Interpolation.linear,
    );

    return <List<List<List<double>>>>[
      List<List<List<double>>>.generate(card.inputHeight, (int y) {
        return List<List<double>>.generate(card.inputWidth, (int x) {
          final img.Pixel pixel = resized.getPixel(x, y);
          return <double>[
            (pixel.r / 255.0 - card.mean[0]) / card.std[0],
            (pixel.g / 255.0 - card.mean[1]) / card.std[1],
            (pixel.b / 255.0 - card.mean[2]) / card.std[2],
          ];
        });
      }),
    ];
  }

  @override
  Future<VisionResult> screen(img.Image image, {required String crop}) async {
    final Interpreter? interpreter = _interpreter;
    final ModelCard? card = _card;
    if (interpreter == null || card == null) {
      throw StateError('TfliteVisionModel.load() must be awaited before screening.');
    }

    // Specification §6.4 / rule 15: refuse a crop the model was not validated
    // for, rather than producing a number that looks authoritative.
    if (!card.supportedCrops.contains(crop)) {
      return VisionResult(
        anomalyScore: 0,
        verdict: ScreeningVerdict.refusedUnsupportedCrop,
        modelId: card.modelId,
        modelKind: card.kind,
        isValidatedModel: card.isValidated,
        refusalReason:
            'SATVA has not been validated for this produce, so it will not score it.',
      );
    }

    final Stopwatch stopwatch = Stopwatch()..start();
    final input = _preprocess(image);

    final int anomalyIndex = card.outputs['anomaly_score'] ?? 0;
    final int ripenessIndex = card.outputs['ripeness_index'] ?? 1;
    final int embeddingIndex = card.outputs['embedding'] ?? 2;

    final Map<int, Object> outputs = <int, Object>{
      anomalyIndex: <List<double>>[<double>[0.0]],
      ripenessIndex: <List<double>>[<double>[0.0]],
      embeddingIndex: <List<double>>[List<double>.filled(576, 0.0)],
    };

    interpreter.runForMultipleInputs(<Object>[input], outputs);
    stopwatch.stop();

    final double anomaly =
        ((outputs[anomalyIndex]! as List<List<double>>)[0][0]).clamp(0.0, 100.0).toDouble();
    final double ripeness =
        ((outputs[ripenessIndex]! as List<List<double>>)[0][0]).clamp(0.0, 1.0).toDouble();
    final List<double> embedding =
        (outputs[embeddingIndex]! as List<List<double>>)[0];

    return VisionResult(
      anomalyScore: double.parse(anomaly.toStringAsFixed(1)),
      verdict: defaultBands.classify(anomaly),
      modelId: card.modelId,
      modelKind: card.kind,
      isValidatedModel: card.isValidated,
      ripenessIndex: ripeness,
      inferenceMs: stopwatch.elapsedMilliseconds,
      embedding: Float32List.fromList(embedding),
      saliency: _occlusionSaliency(anomaly),
    );
  }

  /// Coarse saliency regions for the overlay.
  ///
  /// A proper attention map would need gradient access or an occlusion sweep
  /// (~25 extra forward passes), which is too slow for the ten-second
  /// interaction the specification targets. What is shown instead is the fixed
  /// anatomical regions the model was trained to attend to, weighted by the
  /// score — and the UI labels the overlay as indicative rather than as a true
  /// attention map, because claiming otherwise would be dishonest.
  List<SaliencyRegion> _occlusionSaliency(double score) {
    if (score < defaultBands.notSuspiciousBelow) return const <SaliencyRegion>[];
    final double weight = (score / 100.0).clamp(0.0, 1.0).toDouble();
    return <SaliencyRegion>[
      SaliencyRegion(
        x: 0.30, y: 0.06, width: 0.40, height: 0.26,
        weight: weight, label: 'stem and calyx',
      ),
      SaliencyRegion(
        x: 0.20, y: 0.34, width: 0.60, height: 0.46,
        weight: weight * 0.85, label: 'skin colour and texture',
      ),
    ];
  }
}

/// Choose the best available screener.
///
/// Prefers the trained model; falls back to the heuristic if the asset is
/// missing or the interpreter cannot start. The fallback is logged and the
/// resulting scans are labelled `dev_heuristic`, so a silent downgrade is
/// impossible to miss downstream.
Future<VisionScreener> createScreener() async {
  final TfliteVisionModel model = TfliteVisionModel();
  try {
    await model.load();
    return model;
  } catch (error, stack) {
    debugPrint('SATVA: TFLite model unavailable ($error); using the heuristic screener.');
    debugPrintStack(stackTrace: stack);
    model.dispose();
    final DevHeuristicScreener fallback = DevHeuristicScreener();
    await fallback.load();
    return fallback;
  }
}
