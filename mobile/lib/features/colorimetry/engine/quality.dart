/// Capture-quality gating, on-device.
///
/// Mirrors `backend/app/services/colorimetry/quality.py`.
///
/// Non-negotiable rule 3: bad capture conditions cause **refusal**, not a weak
/// result. Every check here returns a hard pass/fail with a machine-readable
/// reason and a sentence the user can act on. There is no "proceed anyway with
/// lower confidence" path, because a wrong chemical number attached to a
/// complaint is far more damaging than no number at all.
library;

import 'dart:math' as math;

import 'package:image/image.dart' as img;

import 'color_math.dart';
import 'pipeline.dart' show RejectReason;
import 'reference_card.dart';

/// Thresholds are engineering defaults, chosen conservatively. They have **not**
/// been validated against a NABL laboratory (specification §19 lists that as
/// post-hackathon work); they are collected in one class so a calibration
/// campaign can replace them without touching logic.
class QualityThresholds {
  const QualityThresholds({
    this.whitePatchMin = 90.0,
    this.whitePatchMax = 246.0,
    this.maxClippedHighFraction = 0.02,
    this.maxClippedLowFraction = 0.05,
    this.clipHighLevel = 252,
    this.clipLowLevel = 4,
    this.maxNeutralChroma = 14.0,
    this.maxIlluminationNonuniformity = 0.22,
    this.maxStripLStd = 9.0,
    this.minStripPixels = 400,
    this.maxStripClippedFraction = 0.10,
    this.maxMeanResidualDe = 3.5,
    this.maxWorstResidualDe = 7.0,
  });

  final double whitePatchMin;
  final double whitePatchMax;
  final double maxClippedHighFraction;
  final double maxClippedLowFraction;
  final int clipHighLevel;
  final int clipLowLevel;
  final double maxNeutralChroma;
  final double maxIlluminationNonuniformity;
  final double maxStripLStd;
  final int minStripPixels;
  final double maxStripClippedFraction;
  final double maxMeanResidualDe;
  final double maxWorstResidualDe;
}

class QualityReport {
  QualityReport({this.passed = true, this.reason, this.detail, Map<String, dynamic>? metrics})
      : metrics = metrics ?? <String, dynamic>{};

  bool passed;
  String? reason;
  String? detail;
  final Map<String, dynamic> metrics;

  QualityReport fail(String reason, String detail) {
    passed = false;
    this.reason = reason;
    this.detail = detail;
    return this;
  }
}

/// Check that the frame is exposed inside the range the card can correct.
QualityReport assessExposure(
  img.Image warped,
  ReferenceCardSpec spec,
  QualityThresholds thresholds,
) {
  final QualityReport report = QualityReport();
  final List<Patch> neutrals = spec.neutralPatches;
  if (neutrals.isEmpty) {
    return report.fail(
      RejectReason.referenceCardIncomplete,
      'The reference card has no neutral patches.',
    );
  }

  final List<double> luminance = neutrals.map((Patch p) {
    final List<double> rgb = samplePatch(warped, p);
    return (rgb[0] + rgb[1] + rgb[2]) / 3.0;
  }).toList(growable: false);

  final double brightest = luminance.reduce(math.max);
  final double darkest = luminance.reduce(math.min);
  report.metrics['brightest_neutral'] = brightest;
  report.metrics['darkest_neutral'] = darkest;

  // Clipping is measured over the card's *reference* area, excluding the strip
  // well. The strip is the sample, not a reference: an unreacted turmeric strip
  // is a legitimately near-saturated yellow, and counting it as card clipping
  // would refuse exactly the "not detected" readings that clear a vendor.
  final int wellX0 = (spec.stripWell[0] * warped.width).round();
  final int wellY0 = (spec.stripWell[1] * warped.height).round();
  final int wellX1 = ((spec.stripWell[0] + spec.stripWell[2]) * warped.width).round();
  final int wellY1 = ((spec.stripWell[1] + spec.stripWell[3]) * warped.height).round();

  int total = 0, clippedHigh = 0, clippedLow = 0;
  // Sample on a stride: a full-resolution sweep is unnecessary for a fraction
  // and costs real time on a low-end handset.
  for (int y = 0; y < warped.height; y += 3) {
    for (int x = 0; x < warped.width; x += 3) {
      if (x >= wellX0 && x < wellX1 && y >= wellY0 && y < wellY1) continue;
      final img.Pixel pixel = warped.getPixel(x, y);
      total++;
      if (pixel.r >= thresholds.clipHighLevel ||
          pixel.g >= thresholds.clipHighLevel ||
          pixel.b >= thresholds.clipHighLevel) {
        clippedHigh++;
      }
      if (pixel.r <= thresholds.clipLowLevel &&
          pixel.g <= thresholds.clipLowLevel &&
          pixel.b <= thresholds.clipLowLevel) {
        clippedLow++;
      }
    }
  }

  final double highFraction = total == 0 ? 0 : clippedHigh / total;
  final double lowFraction = total == 0 ? 0 : clippedLow / total;
  report.metrics['clipped_high_fraction'] = highFraction;
  report.metrics['clipped_low_fraction'] = lowFraction;

  if (brightest > thresholds.whitePatchMax) {
    return report.fail(
      RejectReason.exposureOutOfRange,
      'The reference white is at ${brightest.toStringAsFixed(0)}/255 — the photo is '
      'over-exposed. Move out of direct sunlight and try again.',
    );
  }
  if (brightest < thresholds.whitePatchMin) {
    return report.fail(
      RejectReason.exposureOutOfRange,
      'The reference white is only ${brightest.toStringAsFixed(0)}/255 — the photo is '
      'too dark. Find brighter light and try again.',
    );
  }
  if (highFraction > thresholds.maxClippedHighFraction) {
    return report.fail(
      RejectReason.clippedHighlights,
      '${(highFraction * 100).toStringAsFixed(1)}% of the card is blown out by glare.',
    );
  }
  if (lowFraction > thresholds.maxClippedLowFraction) {
    return report.fail(
      RejectReason.clippedShadows,
      '${(lowFraction * 100).toStringAsFixed(1)}% of the card is lost in shadow.',
    );
  }

  // The printed ramp descends. If the observed ramp does not, the card was
  // misdetected or something is covering a patch.
  for (int i = 1; i < luminance.length; i++) {
    if (luminance[i] - luminance[i - 1] >= 6.0) {
      return report.fail(
        RejectReason.referenceCardIncomplete,
        'The grey scale on the card does not read correctly — something may be '
        'covering it.',
      );
    }
  }

  return report;
}

/// Check for a tinted light source or a shadow falling across the card.
QualityReport assessIllumination(
  img.Image warped,
  ReferenceCardSpec spec,
  QualityThresholds thresholds,
) {
  final QualityReport report = QualityReport();

  final List<double> chromas = spec.neutralPatches.map((Patch p) {
    final List<double> rgb = samplePatch(warped, p);
    return srgbU8ToLab(rgb[0], rgb[1], rgb[2]).chroma;
  }).toList(growable: false);

  final double meanChroma =
      chromas.reduce((double a, double b) => a + b) / chromas.length;
  report.metrics['neutral_chroma_mean'] = meanChroma;
  report.metrics['neutral_chroma_max'] = chromas.reduce(math.max);

  if (meanChroma > thresholds.maxNeutralChroma) {
    return report.fail(
      RejectReason.illuminantTooTinted,
      'The grey patches show a colour cast (chroma ${meanChroma.toStringAsFixed(1)}). '
      'The light here is too strongly coloured to correct reliably.',
    );
  }

  // Uniformity across the card, measured on the paper white below the patches.
  final int bandTop = (warped.height * 0.90).round();
  final int bandBottom = (warped.height * 0.99).round();
  final int third = warped.width ~/ 3;

  double sumOf(int x0, int x1) {
    double sum = 0;
    int count = 0;
    for (int y = bandTop; y < bandBottom; y += 2) {
      for (int x = x0; x < x1; x += 2) {
        final img.Pixel pixel = warped.getPixel(x, y);
        sum += (pixel.r + pixel.g + pixel.b) / 3.0;
        count++;
      }
    }
    return count == 0 ? 0 : sum / count;
  }

  final double left = sumOf(0, third);
  final double centre = sumOf(third, 2 * third);
  final double right = sumOf(2 * third, warped.width);
  final double high = <double>[left, centre, right].reduce(math.max);
  final double low = <double>[left, centre, right].reduce(math.min);
  final double nonuniformity = high <= 0 ? 0 : (high - low) / high;
  report.metrics['illumination_nonuniformity'] = nonuniformity;

  if (nonuniformity > thresholds.maxIlluminationNonuniformity) {
    return report.fail(
      RejectReason.nonUniformIllumination,
      'Brightness varies by ${(nonuniformity * 100).toStringAsFixed(0)}% across the '
      'card — a shadow is probably falling on it.',
    );
  }

  return report;
}

/// Check that a usable, uniformly reacted strip is present in the well.
QualityReport assessStripRegion(
  img.Image warped,
  ReferenceCardSpec spec,
  QualityThresholds thresholds,
) {
  final QualityReport report = QualityReport();
  final List<int> pixels = sampleRectPixels(warped, spec.stripWell);
  final int pixelCount = pixels.length ~/ 3;
  report.metrics['strip_pixel_count'] = pixelCount;

  if (pixelCount < thresholds.minStripPixels) {
    return report.fail(
      RejectReason.stripRegionNotFound,
      'The strip window is too small in the photo to read reliably. Move closer.',
    );
  }

  // Clipping inside the well destroys the measurement outright: a saturated
  // channel no longer carries the colour information the reading depends on.
  int clippedHigh = 0, clippedLow = 0;
  for (int i = 0; i < pixelCount; i++) {
    final int r = pixels[i * 3], g = pixels[i * 3 + 1], b = pixels[i * 3 + 2];
    if (r >= thresholds.clipHighLevel ||
        g >= thresholds.clipHighLevel ||
        b >= thresholds.clipHighLevel) {
      clippedHigh++;
    }
    if (r <= thresholds.clipLowLevel &&
        g <= thresholds.clipLowLevel &&
        b <= thresholds.clipLowLevel) {
      clippedLow++;
    }
  }
  final double highFraction = clippedHigh / pixelCount;
  final double lowFraction = clippedLow / pixelCount;
  report.metrics['strip_clipped_high_fraction'] = highFraction;
  report.metrics['strip_clipped_low_fraction'] = lowFraction;

  if (highFraction > thresholds.maxStripClippedFraction) {
    return report.fail(
      RejectReason.clippedHighlights,
      '${(highFraction * 100).toStringAsFixed(0)}% of the strip is blown out. Move out '
      'of direct sunlight or glare and retake the photo.',
    );
  }
  if (lowFraction > thresholds.maxStripClippedFraction) {
    return report.fail(
      RejectReason.clippedShadows,
      '${(lowFraction * 100).toStringAsFixed(0)}% of the strip is in deep shadow.',
    );
  }

  // Uniformity of L* across the well.
  double sumL = 0;
  final List<double> lightness = <double>[];
  for (int i = 0; i < pixelCount; i++) {
    final Lab lab = srgbU8ToLab(
      pixels[i * 3].toDouble(),
      pixels[i * 3 + 1].toDouble(),
      pixels[i * 3 + 2].toDouble(),
    );
    lightness.add(lab.l);
    sumL += lab.l;
  }
  final double meanL = sumL / pixelCount;
  double variance = 0;
  for (final double l in lightness) {
    variance += (l - meanL) * (l - meanL);
  }
  final double std = math.sqrt(variance / pixelCount);
  report.metrics['strip_l_std'] = std;

  if (std > thresholds.maxStripLStd) {
    return report.fail(
      RejectReason.stripRegionNotUniform,
      'The strip is not evenly coloured. It may be missing, folded, creased, or '
      'only partly wetted.',
    );
  }

  return report;
}

/// Reject when the colour correction could not fit the observed patches.
///
/// A large residual means the camera response could not be reconciled with the
/// printed card, so any number derived from the corrected strip colour would be
/// fiction. Specification §7 lists this explicitly as a rejection condition.
QualityReport assessCorrectionResidual(
  List<double> residualsDe,
  QualityThresholds thresholds,
) {
  final QualityReport report = QualityReport();
  final double mean =
      residualsDe.reduce((double a, double b) => a + b) / residualsDe.length;
  final double worst = residualsDe.reduce(math.max);
  report.metrics['residual_de_mean'] = mean;
  report.metrics['residual_de_max'] = worst;

  if (mean > thresholds.maxMeanResidualDe) {
    return report.fail(
      RejectReason.correctionResidualTooHigh,
      'Colour correction left an average error of dE ${mean.toStringAsFixed(1)} across '
      'the reference patches — too large to trust a reading.',
    );
  }
  if (worst > thresholds.maxWorstResidualDe) {
    return report.fail(
      RejectReason.correctionResidualTooHigh,
      'One reference patch is off by dE ${worst.toStringAsFixed(1)} after correction.',
    );
  }
  return report;
}
