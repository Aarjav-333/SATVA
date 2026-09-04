/// Cross-implementation parity tests.
///
/// The Python reference in `backend/app/services/colorimetry/` and this Dart
/// port must agree. A reading computed offline on a handset and the same reading
/// recomputed on the server during a dispute have to produce the same number —
/// otherwise "the server disagrees with your phone" becomes an argument nobody
/// can settle.
///
/// Both implementations are tested against the same generated file,
/// `docs/colorimetry/golden_vectors.json`. Regenerate it with
/// `backend/scripts/generate_golden_vectors.py` whenever the maths or a
/// calibration series changes, then run both suites.
library;

import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:satva/features/colorimetry/engine/calibration.dart';
import 'package:satva/features/colorimetry/engine/color_math.dart';
import 'package:satva/features/colorimetry/engine/correction.dart';

late Map<String, dynamic> golden;

/// Sharma, Wu & Dalal (2005) supplementary CIEDE2000 test data.
const List<List<dynamic>> sharmaPairs = <List<dynamic>>[
  <dynamic>[<double>[50.0, 2.6772, -79.7751], <double>[50.0, 0.0, -82.7485], 2.0425],
  <dynamic>[<double>[50.0, 3.1571, -77.2803], <double>[50.0, 0.0, -82.7485], 2.8615],
  <dynamic>[<double>[50.0, 2.8361, -74.0200], <double>[50.0, 0.0, -82.7485], 3.4412],
  <dynamic>[<double>[50.0, -1.3802, -84.2814], <double>[50.0, 0.0, -82.7485], 1.0000],
  <dynamic>[<double>[50.0, 0.0, 0.0], <double>[50.0, -1.0, 2.0], 2.3669],
  <dynamic>[<double>[50.0, 2.4900, -0.0010], <double>[50.0, -2.4900, 0.0009], 7.1792],
  <dynamic>[<double>[50.0, 2.4900, -0.0010], <double>[50.0, -2.4900, 0.0011], 7.2195],
  <dynamic>[<double>[50.0, -0.0010, 2.4900], <double>[50.0, 0.0011, -2.4900], 4.7461],
  <dynamic>[<double>[50.0, 2.5, 0.0], <double>[50.0, 0.0, -2.5], 4.3065],
  <dynamic>[<double>[50.0, 2.5, 0.0], <double>[73.0, 25.0, -18.0], 27.1492],
  <dynamic>[<double>[50.0, 2.5, 0.0], <double>[56.0, -27.0, -3.0], 31.9030],
  <dynamic>[<double>[60.2574, -34.0099, 36.2677], <double>[60.4626, -34.1751, 39.4387], 1.2644],
  <dynamic>[<double>[63.0109, -31.0961, -5.8663], <double>[62.8187, -29.7946, -4.0864], 1.2630],
  <dynamic>[<double>[35.0831, -44.1164, 3.7933], <double>[35.0232, -40.0716, 1.5901], 1.8645],
  <dynamic>[<double>[22.7233, 20.0904, -46.6940], <double>[23.0331, 14.9730, -42.5619], 2.0373],
  <dynamic>[<double>[90.9257, -0.5406, -0.9208], <double>[88.6381, -0.8985, -0.7239], 1.5381],
  <dynamic>[<double>[6.7747, -0.2908, -2.4247], <double>[5.8714, -0.0985, -2.2286], 0.6377],
  <dynamic>[<double>[2.0776, 0.0795, -1.1350], <double>[0.9033, -0.0636, -0.5514], 0.9082],
];

Lab _lab(List<dynamic> v) =>
    Lab((v[0] as num).toDouble(), (v[1] as num).toDouble(), (v[2] as num).toDouble());

void main() {
  setUpAll(() {
    final File file = File('../docs/colorimetry/golden_vectors.json');
    if (!file.existsSync()) {
      throw StateError(
        'Golden vectors not found at ${file.absolute.path}. Run '
        'backend/scripts/generate_golden_vectors.py first.',
      );
    }
    golden = jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
    CalibrationRegistry.fromMap(golden['calibrations'] as Map<String, dynamic>);
  });

  group('CIEDE2000 against published reference data', () {
    for (int i = 0; i < sharmaPairs.length; i++) {
      test('Sharma pair $i', () {
        final List<dynamic> pair = sharmaPairs[i];
        final double got = deltaE2000(
          _lab(pair[0] as List<dynamic>),
          _lab(pair[1] as List<dynamic>),
        );
        expect(got, closeTo(pair[2] as double, 1e-4));
      });
    }

    test('is symmetric', () {
      for (final List<dynamic> pair in sharmaPairs) {
        final Lab a = _lab(pair[0] as List<dynamic>);
        final Lab b = _lab(pair[1] as List<dynamic>);
        expect(deltaE2000(a, b), closeTo(deltaE2000(b, a), 1e-9));
      }
    });

    test('identical colours differ by zero', () {
      for (final List<dynamic> pair in sharmaPairs) {
        final Lab a = _lab(pair[0] as List<dynamic>);
        expect(deltaE2000(a, a), closeTo(0.0, 1e-12));
      }
    });
  });

  group('golden vectors: colour conversion', () {
    test('sRGB u8 -> Lab matches the Python reference exactly', () {
      for (final dynamic raw in golden['srgb_u8_to_lab'] as List<dynamic>) {
        final Map<String, dynamic> caseData = raw as Map<String, dynamic>;
        final List<dynamic> rgb = caseData['rgb'] as List<dynamic>;
        final List<dynamic> expected = caseData['lab'] as List<dynamic>;
        final Lab got = srgbU8ToLab(
          (rgb[0] as num).toDouble(),
          (rgb[1] as num).toDouble(),
          (rgb[2] as num).toDouble(),
        );
        expect(got.l, closeTo((expected[0] as num).toDouble(), 1e-6), reason: 'L for $rgb');
        expect(got.a, closeTo((expected[1] as num).toDouble(), 1e-6), reason: 'a for $rgb');
        expect(got.b, closeTo((expected[2] as num).toDouble(), 1e-6), reason: 'b for $rgb');
      }
    });

    test('white is exactly L*=100 and neutrals are exactly achromatic', () {
      final Lab white = srgbU8ToLab(255, 255, 255);
      expect(white.l, closeTo(100.0, 1e-9));
      expect(white.a.abs(), lessThan(1e-9));
      expect(white.b.abs(), lessThan(1e-9));

      for (final int level in <int>[26, 64, 128, 191, 242]) {
        final Lab grey = srgbU8ToLab(level.toDouble(), level.toDouble(), level.toDouble());
        expect(grey.a.abs(), lessThan(1e-9), reason: 'grey $level should be achromatic');
        expect(grey.b.abs(), lessThan(1e-9), reason: 'grey $level should be achromatic');
      }
    });

    test('sRGB round trip', () {
      for (final List<double> rgb in <List<double>>[
        <double>[0.1, 0.2, 0.3],
        <double>[0.9, 0.5, 0.05],
        <double>[0.5, 0.5, 0.5],
        <double>[0.02, 0.98, 0.44],
      ]) {
        final Lab lab = xyzToLab(srgbToXyz(rgb[0], rgb[1], rgb[2]));
        final List<double> back = labToSrgb(lab);
        for (int i = 0; i < 3; i++) {
          expect(back[i], closeTo(rgb[i], 1e-6));
        }
      }
    });

    test('deltaE2000 golden vectors', () {
      for (final dynamic raw in golden['delta_e_2000'] as List<dynamic>) {
        final Map<String, dynamic> caseData = raw as Map<String, dynamic>;
        final double got = deltaE2000(
          _lab(caseData['lab1'] as List<dynamic>),
          _lab(caseData['lab2'] as List<dynamic>),
        );
        expect(got, closeTo((caseData['de'] as num).toDouble(), 1e-6));
      }
    });
  });

  group('golden vectors: calibration projection', () {
    test('concentration and interval match the Python reference', () {
      for (final dynamic raw in golden['calibration_projection'] as List<dynamic>) {
        final Map<String, dynamic> caseData = raw as Map<String, dynamic>;
        final CalibrationSeries? series =
            CalibrationRegistry.instance[caseData['assay'] as String];
        expect(series, isNotNull, reason: 'unknown assay ${caseData['assay']}');

        final PathProjection projection =
            projectOntoPath(_lab(caseData['lab'] as List<dynamic>), series!);
        expect(
          projection.concentration,
          closeTo((caseData['concentration'] as num).toDouble(), 1e-6),
          reason: 'concentration for ${caseData['assay']}',
        );
        expect(
          projection.segmentIndex,
          equals(caseData['segment_index'] as int),
          reason: 'segment for ${caseData['assay']}',
        );

        final ({double low, double high}) interval = confidenceInterval(
          projection,
          series,
          (caseData['optical_uncertainty_de'] as num).toDouble(),
        );
        expect(interval.low, closeTo((caseData['ci_low'] as num).toDouble(), 1e-4));
        expect(interval.high, closeTo((caseData['ci_high'] as num).toDouble(), 1e-4));
      }
    });

    test('each stop projects to its own concentration', () {
      for (final CalibrationSeries series in CalibrationRegistry.instance.all) {
        for (final CalibrationStop stop in series.stops) {
          final PathProjection projection = projectOntoPath(stop.lab, series);
          expect(projection.concentration, closeTo(stop.concentration, 1e-6));
          expect(projection.offPathDe, closeTo(0.0, 1e-6));
        }
      }
    });

    test('never extrapolates beyond the calibrated range', () {
      for (final CalibrationSeries series in CalibrationRegistry.instance.all) {
        final Lab last = series.stops.last.lab;
        final Lab penultimate = series.stops[series.stops.length - 2].lab;
        final Lab beyond = last + (last - penultimate);
        final PathProjection projection = projectOntoPath(beyond, series);
        expect(projection.concentration, lessThanOrEqualTo(series.highestConcentration + 1e-9));
      }
    });

    test('no shipped series claims laboratory validation', () {
      for (final CalibrationSeries series in CalibrationRegistry.instance.all) {
        expect(
          series.isLabValidated,
          isFalse,
          reason: '${series.assay} must not claim NABL validation',
        );
      }
    });

    test('unknown reagent returns null rather than a default', () {
      expect(CalibrationRegistry.instance['not_a_real_reagent'], isNull);
    });
  });

  group('golden vectors: colour correction', () {
    test('per_channel fit matches the Python reference', () {
      final Map<String, dynamic> caseData =
          (golden['correction'] as Map<String, dynamic>)['per_channel']
              as Map<String, dynamic>;

      final List<List<double>> observed = (caseData['observed'] as List<dynamic>)
          .map((dynamic r) =>
              (r as List<dynamic>).map((dynamic v) => (v as num).toDouble()).toList(),)
          .toList();
      final List<List<double>> expectedPatches = (caseData['expected'] as List<dynamic>)
          .map((dynamic r) =>
              (r as List<dynamic>).map((dynamic v) => (v as num).toDouble()).toList(),)
          .toList();

      final ColorCorrection? correction =
          fitCorrection(observed, expectedPatches, 'per_channel');
      expect(correction, isNotNull);
      expect(
        correction!.meanResidualDe,
        closeTo((caseData['mean_residual_de'] as num).toDouble(), 1e-4),
      );
      expect(
        correction.meanCvResidualDe,
        closeTo((caseData['mean_cv_residual_de'] as num).toDouble(), 1e-4),
      );
    });

    test('identity observation fits an identity correction', () {
      final List<List<double>> patches = <List<double>>[
        <double>[243, 243, 242], <double>[200, 200, 200], <double>[160, 160, 160],
        <double>[122, 122, 121], <double>[85, 85, 85], <double>[52, 52, 52],
        <double>[222, 118, 32], <double>[231, 199, 31], <double>[187, 86, 149],
        <double>[98, 122, 157], <double>[87, 108, 67], <double>[170, 65, 51],
      ];
      final ColorCorrection? correction =
          fitCorrection(patches, patches, 'per_channel');
      expect(correction, isNotNull);
      expect(correction!.meanResidualDe, lessThan(1e-6));
    });

    test('cross-validated residual is never flattering', () {
      final List<List<double>> expectedPatches = <List<double>>[
        <double>[243, 243, 242], <double>[200, 200, 200], <double>[160, 160, 160],
        <double>[122, 122, 121], <double>[85, 85, 85], <double>[52, 52, 52],
        <double>[222, 118, 32], <double>[231, 199, 31], <double>[187, 86, 149],
        <double>[98, 122, 157], <double>[87, 108, 67], <double>[170, 65, 51],
      ];
      final List<List<double>> observed = expectedPatches
          .map((List<double> c) => <double>[
                (c[0] * 1.1 + 3).clamp(0, 255).toDouble(),
                (c[1] * 0.98 - 2).clamp(0, 255).toDouble(),
                (c[2] * 0.87 + 1).clamp(0, 255).toDouble(),
              ],)
          .toList();
      final ColorCorrection? correction =
          fitCorrection(observed, expectedPatches, 'affine33');
      expect(correction, isNotNull);
      expect(
        correction!.meanCvResidualDe,
        greaterThanOrEqualTo(correction.meanResidualDe - 1e-9),
      );
    });

    test('too few patches returns null rather than guessing', () {
      expect(
        fitCorrection(
          <List<double>>[<double>[10, 10, 10]],
          <List<double>>[<double>[12, 12, 12]],
          'per_channel',
        ),
        isNull,
      );
    });
  });
}
