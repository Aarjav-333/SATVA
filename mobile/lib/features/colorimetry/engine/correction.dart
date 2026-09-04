/// Colour correction from observed reference patches to true printed colour.
///
/// Mirrors `backend/app/services/colorimetry/correction.py`.
///
/// The fit is performed in **linear** RGB, because that is the space in which a
/// camera's response to light is approximately affine. Fitting in gamma-encoded
/// sRGB would fold the transfer function into the fit and give a correction only
/// valid at the exposure it was fitted at.
///
/// Two models are tried and the better *cross-validated* one wins. Leave-one-out
/// matters: the 3×3 model has enough freedom to fit 12 patches well while
/// generalising worse to the strip colour, and an in-sample residual would hide
/// exactly that.
library;

import 'dart:math' as math;

import 'color_math.dart';

class ColorCorrection {
  ColorCorrection({
    required this.model,
    required this.matrix,
    required this.offset,
    required this.residualsDe,
    required this.cvResidualsDe,
    required this.nPatches,
  });

  /// 'per_channel' or 'affine33'.
  final String model;

  /// Row-major 3x3.
  final List<List<double>> matrix;
  final List<double> offset;

  final List<double> residualsDe;
  final List<double> cvResidualsDe;
  final int nPatches;

  double get meanResidualDe =>
      residualsDe.reduce((double a, double b) => a + b) / residualsDe.length;

  double get meanCvResidualDe =>
      cvResidualsDe.reduce((double a, double b) => a + b) / cvResidualsDe.length;

  double get worstCvResidualDe => cvResidualsDe.reduce(math.max);

  List<double> applyLinear(List<double> linearRgb) {
    return <double>[
      for (int r = 0; r < 3; r++)
        matrix[r][0] * linearRgb[0] +
            matrix[r][1] * linearRgb[1] +
            matrix[r][2] * linearRgb[2] +
            offset[r],
    ];
  }

  /// Correct an 8-bit sRGB colour and return it as CIE L*a*b*.
  Lab applyToLab(List<double> rgbU8) {
    final List<double> linear = <double>[
      srgbToLinear(rgbU8[0] / 255.0),
      srgbToLinear(rgbU8[1] / 255.0),
      srgbToLinear(rgbU8[2] / 255.0),
    ];
    final List<double> corrected = applyLinear(linear)
        .map((double v) => v.clamp(0.0, 1.0).toDouble())
        .toList(growable: false);
    return _labOfLinear(corrected);
  }

  Map<String, dynamic> toMap() => <String, dynamic>{
        'model': model,
        'n_patches': nPatches,
        'mean_residual_de': meanResidualDe,
        'mean_cv_residual_de': meanCvResidualDe,
        'worst_cv_residual_de': worstCvResidualDe,
      };
}

Lab _labOfLinear(List<double> linearRgb) {
  final double r = linearToSrgb(linearRgb[0]).clamp(0.0, 1.0).toDouble();
  final double g = linearToSrgb(linearRgb[1]).clamp(0.0, 1.0).toDouble();
  final double b = linearToSrgb(linearRgb[2]).clamp(0.0, 1.0).toDouble();
  return xyzToLab(srgbToXyz(r, g, b));
}

/// Least-squares gain and offset per channel, in linear RGB.
({List<List<double>> matrix, List<double> offset}) _fitPerChannel(
  List<List<double>> observed,
  List<List<double>> expected,
) {
  final List<List<double>> matrix = <List<double>>[
    <double>[1, 0, 0],
    <double>[0, 1, 0],
    <double>[0, 0, 1],
  ];
  final List<double> offset = <double>[0, 0, 0];

  for (int c = 0; c < 3; c++) {
    final int n = observed.length;
    double sumX = 0, sumY = 0, sumXX = 0, sumXY = 0;
    for (int i = 0; i < n; i++) {
      final double x = observed[i][c];
      final double y = expected[i][c];
      sumX += x;
      sumY += y;
      sumXX += x * x;
      sumXY += x * y;
    }
    final double denominator = n * sumXX - sumX * sumX;
    if (denominator.abs() < 1e-12) {
      matrix[c][c] = 1.0;
      offset[c] = 0.0;
      continue;
    }
    final double gain = (n * sumXY - sumX * sumY) / denominator;
    matrix[c][c] = gain;
    offset[c] = (sumY - gain * sumX) / n;
  }
  return (matrix: matrix, offset: offset);
}

/// Least-squares 3x3 matrix plus offset, solved by normal equations.
({List<List<double>> matrix, List<double> offset})? _fitAffine33(
  List<List<double>> observed,
  List<List<double>> expected,
) {
  const int k = 4; // r, g, b, 1
  final List<List<double>> ata =
      List<List<double>>.generate(k, (_) => List<double>.filled(k, 0.0));
  final List<List<double>> atb =
      List<List<double>>.generate(k, (_) => List<double>.filled(3, 0.0));

  for (int i = 0; i < observed.length; i++) {
    final List<double> row = <double>[
      observed[i][0],
      observed[i][1],
      observed[i][2],
      1.0,
    ];
    for (int a = 0; a < k; a++) {
      for (int b = 0; b < k; b++) {
        ata[a][b] += row[a] * row[b];
      }
      for (int c = 0; c < 3; c++) {
        atb[a][c] += row[a] * expected[i][c];
      }
    }
  }

  final List<List<double>>? solution = _solve(ata, atb);
  if (solution == null) return null;

  return (
    matrix: <List<double>>[
      <double>[solution[0][0], solution[1][0], solution[2][0]],
      <double>[solution[0][1], solution[1][1], solution[2][1]],
      <double>[solution[0][2], solution[1][2], solution[2][2]],
    ],
    offset: <double>[solution[3][0], solution[3][1], solution[3][2]],
  );
}

/// Gaussian elimination with partial pivoting. Returns null if singular.
List<List<double>>? _solve(List<List<double>> a, List<List<double>> b) {
  final int n = a.length;
  final int m = b[0].length;
  final List<List<double>> matrix =
      List<List<double>>.generate(n, (int i) => <double>[...a[i], ...b[i]]);

  for (int col = 0; col < n; col++) {
    int pivot = col;
    for (int row = col + 1; row < n; row++) {
      if (matrix[row][col].abs() > matrix[pivot][col].abs()) pivot = row;
    }
    if (matrix[pivot][col].abs() < 1e-12) return null;
    final List<double> tmp = matrix[col];
    matrix[col] = matrix[pivot];
    matrix[pivot] = tmp;

    final double diagonal = matrix[col][col];
    for (int j = col; j < n + m; j++) {
      matrix[col][j] /= diagonal;
    }
    for (int row = 0; row < n; row++) {
      if (row == col) continue;
      final double factor = matrix[row][col];
      if (factor == 0) continue;
      for (int j = col; j < n + m; j++) {
        matrix[row][j] -= factor * matrix[col][j];
      }
    }
  }

  return List<List<double>>.generate(
    n,
    (int i) => matrix[i].sublist(n, n + m),
  );
}

List<List<double>> _toLinear(List<List<double>> rgbU8) {
  return rgbU8
      .map((List<double> c) => <double>[
            srgbToLinear(c[0] / 255.0),
            srgbToLinear(c[1] / 255.0),
            srgbToLinear(c[2] / 255.0),
          ],)
      .toList(growable: false);
}

ColorCorrection? fitCorrection(
  List<List<double>> observedU8,
  List<List<double>> expectedU8,
  String model,
) {
  final int n = observedU8.length;
  final int minimum = model == 'affine33' ? 4 : 2;
  if (n < minimum) return null;

  final List<List<double>> observedLinear = _toLinear(observedU8);
  final List<List<double>> expectedLinear = _toLinear(expectedU8);
  final List<Lab> expectedLab = expectedU8
      .map((List<double> c) => srgbU8ToLab(c[0], c[1], c[2]))
      .toList(growable: false);

  ({List<List<double>> matrix, List<double> offset})? fit;
  if (model == 'per_channel') {
    fit = _fitPerChannel(observedLinear, expectedLinear);
  } else {
    fit = _fitAffine33(observedLinear, expectedLinear);
  }
  if (fit == null) return null;

  List<double> apply(List<List<double>> m, List<double> o, List<double> v) => <double>[
        for (int r = 0; r < 3; r++)
          m[r][0] * v[0] + m[r][1] * v[1] + m[r][2] * v[2] + o[r],
      ];

  final List<double> residuals = <double>[
    for (int i = 0; i < n; i++)
      deltaE2000(
        _labOfLinear(apply(fit.matrix, fit.offset, observedLinear[i])),
        expectedLab[i],
      ),
  ];

  // Leave-one-out: refit without patch i, then measure the error on patch i.
  final List<double> cvResiduals = <double>[];
  if (n > minimum) {
    for (int i = 0; i < n; i++) {
      final List<List<double>> keepObserved = <List<double>>[
        for (int j = 0; j < n; j++)
          if (j != i) observedLinear[j],
      ];
      final List<List<double>> keepExpected = <List<double>>[
        for (int j = 0; j < n; j++)
          if (j != i) expectedLinear[j],
      ];
      final fold = model == 'per_channel'
          ? _fitPerChannel(keepObserved, keepExpected)
          : _fitAffine33(keepObserved, keepExpected);
      if (fold == null) {
        cvResiduals.add(residuals[i]);
        continue;
      }
      cvResiduals.add(
        deltaE2000(
          _labOfLinear(apply(fold.matrix, fold.offset, observedLinear[i])),
          expectedLab[i],
        ),
      );
    }
  } else {
    cvResiduals.addAll(residuals);
  }

  return ColorCorrection(
    model: model,
    matrix: fit.matrix,
    offset: fit.offset,
    residualsDe: residuals,
    cvResidualsDe: cvResiduals,
    nPatches: n,
  );
}

/// Fit every candidate model and keep the best cross-validated one.
ColorCorrection? fitBestCorrection(
  List<List<double>> observedU8,
  List<List<double>> expectedU8,
) {
  final List<ColorCorrection> fitted = <ColorCorrection>[
    for (final String model in <String>['per_channel', 'affine33'])
      if (fitCorrection(observedU8, expectedU8, model) case final ColorCorrection c) c,
  ];
  if (fitted.isEmpty) return null;
  fitted.sort((ColorCorrection a, ColorCorrection b) =>
      a.meanCvResidualDe.compareTo(b.meanCvResidualDe),);
  return fitted.first;
}
