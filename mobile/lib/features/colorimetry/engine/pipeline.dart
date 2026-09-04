/// The on-device SATVA colorimetric pipeline.
///
/// Mirrors `backend/app/services/colorimetry/pipeline.py` step for step, so the
/// handset can produce an evidence-grade reading with no connectivity at all —
/// which is what makes SATVA usable in a market (specification §5.1).
///
/// Implements specification §7 in order: detect the card, verify exposure and
/// lighting, observe the reference patches, fit a colour correction, apply it to
/// the strip, convert to CIE L*a*b*, compute ΔE against the calibrated stops,
/// produce a number, attach a confidence interval, and **reject** if the
/// correction residual is excessive.
///
/// Every failure path returns a result with `accepted == false` and a
/// machine-readable reason. The pipeline never returns a number it does not
/// stand behind — non-negotiable rule 3, and the single most important
/// behaviour in this file.
library;

import 'package:image/image.dart' as img;

import 'calibration.dart';
import 'color_math.dart';
import 'correction.dart';
import 'quality.dart';
import 'reference_card.dart';

const String pipelineVersion = '1.0.0';

/// Machine-readable refusal reasons. Must match
/// `app.core.constants.ColorimetryRejectReason` on the server.
class RejectReason {
  static const String referenceCardNotFound = 'reference_card_not_found';
  static const String referenceCardIncomplete = 'reference_card_incomplete';
  static const String exposureOutOfRange = 'exposure_out_of_range';
  static const String clippedHighlights = 'clipped_highlights';
  static const String clippedShadows = 'clipped_shadows';
  static const String illuminantTooTinted = 'illuminant_too_tinted';
  static const String nonUniformIllumination = 'non_uniform_illumination';
  static const String correctionResidualTooHigh = 'correction_residual_too_high';
  static const String stripRegionNotFound = 'strip_region_not_found';
  static const String stripRegionNotUniform = 'strip_region_not_uniform';
  static const String outOfCalibrationRange = 'out_of_calibration_range';
  static const String unknownReagent = 'unknown_reagent';
}

class ColorimetryResult {
  const ColorimetryResult({
    required this.accepted,
    required this.assay,
    this.calibrationId,
    this.concentration,
    this.unit,
    this.ciLow,
    this.ciHigh,
    this.bandLabel,
    this.exceedsActionThreshold = false,
    this.isLabValidated = false,
    this.atRangeLimit = false,
    this.rangeLimitSide,
    this.rejectReason,
    this.rejectDetail,
    this.stripLabRaw,
    this.stripLabCorrected,
    this.deltaENearestStop,
    this.offPathDeltaE,
    this.correctionResidualDe,
    this.correctionModel,
    this.exposureScore,
    this.illuminantTint,
    this.cardAreaFraction,
    this.elapsedMs,
    this.quality = const <String, dynamic>{},
  });

  final bool accepted;
  final String assay;
  final String? calibrationId;

  final double? concentration;
  final String? unit;
  final double? ciLow;
  final double? ciHigh;
  final String? bandLabel;
  final bool exceedsActionThreshold;
  final bool isLabValidated;
  final bool atRangeLimit;
  final String? rangeLimitSide;

  final String? rejectReason;
  final String? rejectDetail;

  final Lab? stripLabRaw;
  final Lab? stripLabCorrected;
  final double? deltaENearestStop;
  final double? offPathDeltaE;
  final double? correctionResidualDe;
  final String? correctionModel;
  final double? exposureScore;
  final double? illuminantTint;
  final double? cardAreaFraction;
  final int? elapsedMs;
  final Map<String, dynamic> quality;

  factory ColorimetryResult.refused(
    String assay,
    String reason,
    String detail, {
    String? calibrationId,
    Map<String, dynamic> quality = const <String, dynamic>{},
    int? elapsedMs,
  }) {
    return ColorimetryResult(
      accepted: false,
      assay: assay,
      calibrationId: calibrationId,
      rejectReason: reason,
      rejectDetail: detail,
      quality: quality,
      elapsedMs: elapsedMs,
    );
  }

  /// Payload for `POST /colorimetry/scans/{id}/readings`.
  Map<String, dynamic> toApiPayload() => <String, dynamic>{
        'assay': assay,
        'accepted': accepted,
        'calibration_id': calibrationId ?? '',
        'pipeline_version': pipelineVersion,
        'reject_reason': rejectReason,
        'reject_detail': rejectDetail,
        'concentration_value': concentration,
        'concentration_unit': unit,
        'ci_low': ciLow,
        'ci_high': ciHigh,
        'band_label': bandLabel,
        'exceeds_action_threshold': exceedsActionThreshold,
        'delta_e_nearest': deltaENearestStop,
        'strip_lab': stripLabCorrected?.toList(),
        'correction_residual_de': correctionResidualDe,
        'exposure_score': exposureScore,
        'illuminant_tint': illuminantTint,
        'quality': quality,
      };
}

/// Read a reacted test strip photographed beside the SATVA reference card.
ColorimetryResult readStrip(
  img.Image frame,
  String assay, {
  ReferenceCardSpec? spec,
  QualityThresholds thresholds = const QualityThresholds(),
}) {
  final Stopwatch stopwatch = Stopwatch()..start();
  final ReferenceCardSpec card = spec ?? satvaCardV1;

  int elapsed() => stopwatch.elapsedMilliseconds;

  // --- Reagent lookup ----------------------------------------------------
  final CalibrationSeries? series = CalibrationRegistry.isLoaded
      ? CalibrationRegistry.instance[assay]
      : null;
  if (series == null) {
    return ColorimetryResult.refused(
      assay,
      RejectReason.unknownReagent,
      "No calibration series is registered for reagent '$assay'.",
      elapsedMs: elapsed(),
    );
  }

  // --- Step 1: detect the reference card ---------------------------------
  final CardDetection detection = detectCard(frame, card);
  if (!detection.found || detection.warped == null) {
    final String reason = detection.reason == 'orientation_key_not_found'
        ? RejectReason.referenceCardIncomplete
        : RejectReason.referenceCardNotFound;
    return ColorimetryResult.refused(
      assay,
      reason,
      'The SATVA reference card was not found in the photograph. Place the card '
      'flat beside the strip and retake the photo.',
      calibrationId: series.calibrationId,
      quality: <String, dynamic>{'detection': detection.reason},
      elapsedMs: elapsed(),
    );
  }

  final img.Image warped = detection.warped!;
  final Map<String, dynamic> quality = <String, dynamic>{
    'card_area_fraction': detection.quadAreaFraction,
    'orientation_confidence': detection.orientationConfidence,
  };

  // --- Step 2: verify exposure, lighting and the strip region ------------
  for (final QualityReport report in <QualityReport>[
    assessExposure(warped, card, thresholds),
    assessIllumination(warped, card, thresholds),
    assessStripRegion(warped, card, thresholds),
  ]) {
    quality.addAll(report.metrics);
    if (!report.passed) {
      return ColorimetryResult.refused(
        assay,
        report.reason!,
        report.detail!,
        calibrationId: series.calibrationId,
        quality: quality,
        elapsedMs: elapsed(),
      );
    }
  }

  // --- Steps 3-4: observe the patches and fit the correction -------------
  final List<List<double>> observed = card.patches
      .map((Patch p) => samplePatch(warped, p))
      .toList(growable: false);
  final List<List<double>> expected = card.patches
      .map((Patch p) => p.srgb.map((int v) => v.toDouble()).toList())
      .toList(growable: false);

  final ColorCorrection? correction = fitBestCorrection(observed, expected);
  if (correction == null) {
    return ColorimetryResult.refused(
      assay,
      RejectReason.correctionResidualTooHigh,
      'Colour correction could not be fitted to the reference patches.',
      calibrationId: series.calibrationId,
      quality: quality,
      elapsedMs: elapsed(),
    );
  }
  quality['correction'] = correction.toMap();

  // --- Step 10, applied early as the specification requires --------------
  // The residual gate runs before any number is produced, so a badly corrected
  // frame can never yield a reading at all.
  final QualityReport residualCheck =
      assessCorrectionResidual(correction.cvResidualsDe, thresholds);
  quality.addAll(residualCheck.metrics);
  if (!residualCheck.passed) {
    return ColorimetryResult.refused(
      assay,
      residualCheck.reason!,
      residualCheck.detail!,
      calibrationId: series.calibrationId,
      quality: quality,
      elapsedMs: elapsed(),
    );
  }

  // --- Steps 5-6: correct the strip and convert to Lab -------------------
  final List<double> stripRaw = sampleRect(warped, card.stripWell);
  final Lab stripLabRaw = srgbU8ToLab(stripRaw[0], stripRaw[1], stripRaw[2]);
  final Lab stripLabCorrected = correction.applyToLab(stripRaw);

  // --- Step 7: dE against the calibrated stops ---------------------------
  final PathProjection projection = projectOntoPath(stripLabCorrected, series);
  if (projection.offPathDe > series.maxOffPathDe) {
    return ColorimetryResult.refused(
      assay,
      RejectReason.outOfCalibrationRange,
      'The strip colour sits dE ${projection.offPathDe.toStringAsFixed(1)} away from '
      'the ${series.displayName} calibration path. This does not look like a '
      'reacted strip for the selected test.',
      calibrationId: series.calibrationId,
      quality: <String, dynamic>{...quality, 'off_path_de': projection.offPathDe},
      elapsedMs: elapsed(),
    );
  }

  // --- Steps 8-9: the number, and the interval around it -----------------
  final double opticalUncertainty = correction.meanCvResidualDe;
  final ({double low, double high}) interval =
      confidenceInterval(projection, series, opticalUncertainty);
  final double concentration =
      double.parse(projection.concentration.toStringAsFixed(4));

  const double tolerance = 1e-6;
  final bool atUpper = concentration >= series.highestConcentration - tolerance;
  final bool atLower = concentration <= series.lowestConcentration + tolerance;

  return ColorimetryResult(
    accepted: true,
    assay: assay,
    calibrationId: series.calibrationId,
    concentration: concentration,
    unit: series.unit,
    ciLow: interval.low,
    ciHigh: interval.high,
    bandLabel: series.bandLabel(concentration),
    // The action threshold is compared against the *lower* bound. Escalating on
    // the point estimate alone would flag readings whose uncertainty still spans
    // "not detected", and a complaint against an honest vendor is exactly the
    // harm rule 1 exists to prevent.
    exceedsActionThreshold: interval.low >= series.actionThreshold,
    isLabValidated: series.isLabValidated,
    atRangeLimit: atUpper || atLower,
    rangeLimitSide: atUpper ? 'upper' : (atLower ? 'lower' : null),
    stripLabRaw: stripLabRaw,
    stripLabCorrected: stripLabCorrected,
    deltaENearestStop: projection.nearestStopDe,
    offPathDeltaE: projection.offPathDe,
    correctionResidualDe: opticalUncertainty,
    correctionModel: correction.model,
    exposureScore: (quality['brightest_neutral'] as num?)?.toDouble(),
    illuminantTint: (quality['neutral_chroma_mean'] as num?)?.toDouble(),
    cardAreaFraction: detection.quadAreaFraction,
    elapsedMs: elapsed(),
    quality: quality,
  );
}
