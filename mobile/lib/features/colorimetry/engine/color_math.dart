/// Colour-space mathematics for the on-device colorimetric reader.
///
/// This is a deliberate, line-for-line mirror of
/// `backend/app/services/colorimetry/color_math.py`. The two implementations
/// are checked against the same golden vectors in
/// `docs/colorimetry/golden_vectors.json`, so they cannot silently diverge —
/// and if they ever do, a test fails rather than a reading quietly changing
/// depending on whether it was computed on the handset or on the server.
///
/// Everything here is pure and deterministic: no I/O, no platform channels, no
/// dependence on device state.
library;

import 'dart:math' as math;

/// sRGB (IEC 61966-2-1) to CIE XYZ, D65.
const List<List<double>> _srgbToXyz = <List<double>>[
  <double>[0.4124564, 0.3575761, 0.1804375],
  <double>[0.2126729, 0.7151522, 0.0721750],
  <double>[0.0193339, 0.1191920, 0.9503041],
];

/// D65 white point, derived from the matrix above rather than written out as
/// the nominal (95.047, 100.000, 108.883).
///
/// The published matrix coefficients are rounded and do not sum exactly to that
/// triple. Using the nominal value leaves white at L* = 100.0000039 and gives
/// neutral greys a chroma of ~4e-6 — small, but it means "is this patch
/// neutral?" is answered against an inconsistent reference. Deriving the white
/// point from the same matrix makes white exact by construction.
final List<double> d65White = <double>[
  (_srgbToXyz[0][0] + _srgbToXyz[0][1] + _srgbToXyz[0][2]) * 100.0,
  (_srgbToXyz[1][0] + _srgbToXyz[1][1] + _srgbToXyz[1][2]) * 100.0,
  (_srgbToXyz[2][0] + _srgbToXyz[2][1] + _srgbToXyz[2][2]) * 100.0,
];

const double _cieEpsilon = 216.0 / 24389.0;
const double _cieKappa = 24389.0 / 27.0;

/// A CIE L*a*b* colour.
class Lab {
  const Lab(this.l, this.a, this.b);

  final double l;
  final double a;
  final double b;

  double get chroma => math.sqrt(a * a + b * b);

  double get hueDegrees {
    final double h = _degrees(math.atan2(b, a));
    return h < 0 ? h + 360.0 : h;
  }

  List<double> toList() => <double>[l, a, b];

  Lab operator +(Lab other) => Lab(l + other.l, a + other.a, b + other.b);

  Lab operator -(Lab other) => Lab(l - other.l, a - other.a, b - other.b);

  Lab operator *(double k) => Lab(l * k, a * k, b * k);

  /// Euclidean distance in Lab. Used for geometric projection onto the
  /// calibration path, never for reporting a colour difference.
  double distanceTo(Lab other) {
    final double dl = l - other.l;
    final double da = a - other.a;
    final double db = b - other.b;
    return math.sqrt(dl * dl + da * da + db * db);
  }

  @override
  String toString() =>
      'Lab(${l.toStringAsFixed(3)}, ${a.toStringAsFixed(3)}, ${b.toStringAsFixed(3)})';
}

double _degrees(double radians) => radians * 180.0 / math.pi;

double _radians(double degrees) => degrees * math.pi / 180.0;

/// Undo the sRGB transfer function. Input and output in [0, 1].
double srgbToLinear(double channel) {
  return channel <= 0.04045
      ? channel / 12.92
      : math.pow((channel + 0.055) / 1.055, 2.4).toDouble();
}

/// Apply the sRGB transfer function. Input and output in [0, 1].
double linearToSrgb(double channel) {
  final double c = channel.clamp(0.0, double.infinity).toDouble();
  return c <= 0.0031308 ? c * 12.92 : 1.055 * math.pow(c, 1 / 2.4).toDouble() - 0.055;
}

/// sRGB in [0, 1] to CIE XYZ with Y in [0, 100].
List<double> srgbToXyz(double r, double g, double b) {
  final double lr = srgbToLinear(r);
  final double lg = srgbToLinear(g);
  final double lb = srgbToLinear(b);
  return <double>[
    (_srgbToXyz[0][0] * lr + _srgbToXyz[0][1] * lg + _srgbToXyz[0][2] * lb) * 100.0,
    (_srgbToXyz[1][0] * lr + _srgbToXyz[1][1] * lg + _srgbToXyz[1][2] * lb) * 100.0,
    (_srgbToXyz[2][0] * lr + _srgbToXyz[2][1] * lg + _srgbToXyz[2][2] * lb) * 100.0,
  ];
}

double _f(double ratio) => ratio > _cieEpsilon
    ? math.pow(ratio.clamp(0.0, double.infinity), 1.0 / 3.0).toDouble()
    : (_cieKappa * ratio + 16.0) / 116.0;

/// CIE XYZ to CIE L*a*b*.
Lab xyzToLab(List<double> xyz) {
  final double fx = _f(xyz[0] / d65White[0]);
  final double fy = _f(xyz[1] / d65White[1]);
  final double fz = _f(xyz[2] / d65White[2]);
  return Lab(116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz));
}

/// CIE L*a*b* back to CIE XYZ.
List<double> labToXyz(Lab lab) {
  final double fy = (lab.l + 16.0) / 116.0;
  final double fx = fy + lab.a / 500.0;
  final double fz = fy - lab.b / 200.0;

  double inv(double t) {
    final double t3 = t * t * t;
    return t3 > _cieEpsilon ? t3 : (116.0 * t - 16.0) / _cieKappa;
  }

  return <double>[
    inv(fx) * d65White[0],
    inv(fy) * d65White[1],
    inv(fz) * d65White[2],
  ];
}

/// 8-bit sRGB straight to L*a*b*.
Lab srgbU8ToLab(double r, double g, double b) =>
    xyzToLab(srgbToXyz(r / 255.0, g / 255.0, b / 255.0));

/// L*a*b* to sRGB in [0, 1], clipped to the displayable gamut.
List<double> labToSrgb(Lab lab) {
  final List<double> xyz = labToXyz(lab);
  final double x = xyz[0] / 100.0;
  final double y = xyz[1] / 100.0;
  final double z = xyz[2] / 100.0;

  // Inverse of _srgbToXyz, computed once as constants so the Dart and Python
  // implementations use identical numbers.
  const double m00 = 3.2404542, m01 = -1.5371385, m02 = -0.4985314;
  const double m10 = -0.9692660, m11 = 1.8760108, m12 = 0.0415560;
  const double m20 = 0.0556434, m21 = -0.2040259, m22 = 1.0572252;

  final double lr = (m00 * x + m01 * y + m02 * z).clamp(0.0, 1.0).toDouble();
  final double lg = (m10 * x + m11 * y + m12 * z).clamp(0.0, 1.0).toDouble();
  final double lb = (m20 * x + m21 * y + m22 * z).clamp(0.0, 1.0).toDouble();

  return <double>[
    linearToSrgb(lr).clamp(0.0, 1.0).toDouble(),
    linearToSrgb(lg).clamp(0.0, 1.0).toDouble(),
    linearToSrgb(lb).clamp(0.0, 1.0).toDouble(),
  ];
}

/// CIE76 Euclidean distance. Coarse gating only, never reported.
double deltaE76(Lab a, Lab b) => a.distanceTo(b);

/// CIEDE2000 colour difference.
///
/// Follows Sharma, Wu & Dalal (2005), including the hue-discontinuity handling
/// that naive implementations get wrong. The Python reference is checked
/// against that paper's 34 published test pairs; this port is checked against
/// the same values through the shared golden vectors.
double deltaE2000(
  Lab lab1,
  Lab lab2, {
  double kL = 1.0,
  double kC = 1.0,
  double kH = 1.0,
}) {
  final double c1 = math.sqrt(lab1.a * lab1.a + lab1.b * lab1.b);
  final double c2 = math.sqrt(lab2.a * lab2.a + lab2.b * lab2.b);
  final double cBar = (c1 + c2) / 2.0;

  final double cBar7 = math.pow(cBar, 7).toDouble();
  final double pow25_7 = math.pow(25.0, 7).toDouble();
  final double g = 0.5 * (1.0 - math.sqrt(cBar7 / (cBar7 + pow25_7)));

  final double a1p = (1.0 + g) * lab1.a;
  final double a2p = (1.0 + g) * lab2.a;
  final double c1p = math.sqrt(a1p * a1p + lab1.b * lab1.b);
  final double c2p = math.sqrt(a2p * a2p + lab2.b * lab2.b);

  double hue(double ap, double bp) {
    if (ap.abs() < 1e-12 && bp.abs() < 1e-12) return 0.0;
    final double h = _degrees(math.atan2(bp, ap));
    return h < 0 ? h + 360.0 : h;
  }

  final double h1p = hue(a1p, lab1.b);
  final double h2p = hue(a2p, lab2.b);

  final double dLp = lab2.l - lab1.l;
  final double dCp = c2p - c1p;

  final bool chromaProductZero = (c1p * c2p) == 0;
  final double dhpRaw = h2p - h1p;
  double dhp;
  if (chromaProductZero) {
    dhp = 0.0;
  } else if (dhpRaw.abs() <= 180.0) {
    dhp = dhpRaw;
  } else if (dhpRaw > 180.0) {
    dhp = dhpRaw - 360.0;
  } else {
    dhp = dhpRaw + 360.0;
  }
  final double dHp = 2.0 * math.sqrt(c1p * c2p) * math.sin(_radians(dhp / 2.0));

  final double lpBar = (lab1.l + lab2.l) / 2.0;
  final double cpBar = (c1p + c2p) / 2.0;

  final double hSum = h1p + h2p;
  final double hAbs = (h1p - h2p).abs();
  double hpBar;
  if (chromaProductZero) {
    hpBar = hSum;
  } else if (hAbs <= 180.0) {
    hpBar = hSum / 2.0;
  } else if (hSum < 360.0) {
    hpBar = (hSum + 360.0) / 2.0;
  } else {
    hpBar = (hSum - 360.0) / 2.0;
  }

  final double t = 1.0 -
      0.17 * math.cos(_radians(hpBar - 30.0)) +
      0.24 * math.cos(_radians(2.0 * hpBar)) +
      0.32 * math.cos(_radians(3.0 * hpBar + 6.0)) -
      0.20 * math.cos(_radians(4.0 * hpBar - 63.0));

  final double dTheta = 30.0 * math.exp(-math.pow((hpBar - 275.0) / 25.0, 2).toDouble());
  final double cpBar7 = math.pow(cpBar, 7).toDouble();
  final double rC = 2.0 * math.sqrt(cpBar7 / (cpBar7 + pow25_7));
  final double rT = -rC * math.sin(_radians(2.0 * dTheta));

  final double lpBarSq = (lpBar - 50.0) * (lpBar - 50.0);
  final double sL = 1.0 + (0.015 * lpBarSq) / math.sqrt(20.0 + lpBarSq);
  final double sC = 1.0 + 0.045 * cpBar;
  final double sH = 1.0 + 0.015 * cpBar * t;

  final double termL = dLp / (kL * sL);
  final double termC = dCp / (kC * sC);
  final double termH = dHp / (kH * sH);

  return math.sqrt(
    termL * termL + termC * termC + termH * termH + rT * termC * termH,
  );
}

/// Perceptual mean of a block of 8-bit RGB pixels.
///
/// Averaging happens in linear light, not in gamma-encoded sRGB: the mean of
/// gamma-encoded values is systematically darker than the mean of the light the
/// sensor actually received.
Lab meanLab(List<int> rgbBytes) {
  if (rgbBytes.isEmpty) return const Lab(0, 0, 0);
  final int pixelCount = rgbBytes.length ~/ 3;
  double sumR = 0, sumG = 0, sumB = 0;
  for (int i = 0; i < pixelCount; i++) {
    sumR += srgbToLinear(rgbBytes[i * 3] / 255.0);
    sumG += srgbToLinear(rgbBytes[i * 3 + 1] / 255.0);
    sumB += srgbToLinear(rgbBytes[i * 3 + 2] / 255.0);
  }
  final double lr = sumR / pixelCount;
  final double lg = sumG / pixelCount;
  final double lb = sumB / pixelCount;
  return xyzToLab(<double>[
    (_srgbToXyz[0][0] * lr + _srgbToXyz[0][1] * lg + _srgbToXyz[0][2] * lb) * 100.0,
    (_srgbToXyz[1][0] * lr + _srgbToXyz[1][1] * lg + _srgbToXyz[1][2] * lb) * 100.0,
    (_srgbToXyz[2][0] * lr + _srgbToXyz[2][1] * lg + _srgbToXyz[2][2] * lb) * 100.0,
  ]);
}
