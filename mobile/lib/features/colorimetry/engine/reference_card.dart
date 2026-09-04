/// The SATVA reference card and its detection, on-device.
///
/// Mirrors `backend/app/services/colorimetry/reference_card.py`. The card
/// geometry, patch values and strip-well position must match the printed card
/// and the server implementation exactly — a mismatch would silently sample the
/// wrong pixels and produce a confident, wrong number.
///
/// Detection uses the `image` package rather than OpenCV: it is pure Dart, so
/// it works on every Android device without an FFI build, and the operations
/// needed (greyscale, threshold, contour walk, perspective warp) are simple
/// enough that the dependency is not worth the packaging cost.
library;

import 'dart:math' as math;

import 'package:image/image.dart' as img;

import 'color_math.dart';

const double cardWidthMm = 85.6;
const double cardHeightMm = 54.0;
const double cardAspect = cardWidthMm / cardHeightMm;

/// Canonical warp size, matching the Python reference.
const int warpWidth = 856;
const int warpHeight = 540;

class Patch {
  const Patch({
    required this.name,
    required this.x,
    required this.y,
    required this.w,
    required this.h,
    required this.srgb,
    this.isNeutral = false,
  });

  final String name;
  final double x;
  final double y;
  final double w;
  final double h;
  final List<int> srgb;
  final bool isNeutral;

  Lab get lab => srgbU8ToLab(
        srgb[0].toDouble(),
        srgb[1].toDouble(),
        srgb[2].toDouble(),
      );
}

class ReferenceCardSpec {
  const ReferenceCardSpec({
    required this.cardId,
    required this.version,
    required this.patches,
    required this.stripWell,
    required this.orientationKey,
  });

  final String cardId;
  final String version;
  final List<Patch> patches;

  /// (x, y, w, h) in normalised card coordinates.
  final List<double> stripWell;

  /// Breaks the card's rotational symmetry so an upside-down card is
  /// re-oriented rather than misread.
  final Patch orientationKey;

  List<Patch> get neutralPatches =>
      patches.where((Patch p) => p.isNeutral).toList(growable: false);

  List<Patch> get chromaticPatches =>
      patches.where((Patch p) => !p.isNeutral).toList(growable: false);
}

// Neutral ramp: six steps spanning the exposure range. Nothing is printed at
// 255 or 0, because a clipped reference patch carries no information about how
// badly the frame is clipped.
const List<List<int>> _neutrals = <List<int>>[
  <int>[243, 243, 242],
  <int>[200, 200, 200],
  <int>[160, 160, 160],
  <int>[122, 122, 121],
  <int>[85, 85, 85],
  <int>[52, 52, 52],
];

// Chromatic patches bracketing the turmeric reaction path (yellow to
// red-brown), plus green and blue anchors so the correction fit is constrained
// across the gamut rather than only along the reaction path.
const List<List<int>> _chromatics = <List<int>>[
  <int>[222, 118, 32],
  <int>[231, 199, 31],
  <int>[187, 86, 149],
  <int>[98, 122, 157],
  <int>[87, 108, 67],
  <int>[170, 65, 51],
];

List<Patch> _row(
  String prefix,
  double y,
  List<List<int>> values, {
  required double x0,
  required bool neutral,
  double patchW = 0.085,
  double patchH = 0.24,
  double gap = 0.015,
}) {
  return <Patch>[
    for (int i = 0; i < values.length; i++)
      Patch(
        name: '$prefix$i',
        x: x0 + i * (patchW + gap),
        y: y,
        w: patchW,
        h: patchH,
        srgb: values[i],
        isNeutral: neutral,
      ),
  ];
}

final ReferenceCardSpec satvaCardV1 = ReferenceCardSpec(
  cardId: 'satva-refcard',
  version: 'v1',
  orientationKey: const Patch(
    name: 'KEY',
    x: 0.020,
    y: 0.060,
    w: 0.060,
    h: 0.14,
    srgb: <int>[24, 58, 168],
  ),
  patches: <Patch>[
    ..._row('N', 0.075, _neutrals, x0: 0.100, neutral: true),
    ..._row('C', 0.400, _chromatics, x0: 0.100, neutral: false),
  ],
  stripWell: const <double>[0.180, 0.700, 0.560, 0.200],
);

class CardDetection {
  const CardDetection({
    required this.found,
    this.warped,
    this.quadAreaFraction = 0.0,
    this.orientationConfidence = 0.0,
    this.reason,
  });

  final bool found;
  final img.Image? warped;
  final double quadAreaFraction;
  final double orientationConfidence;
  final String? reason;
}

class _Point {
  const _Point(this.x, this.y);
  final double x;
  final double y;
}

/// Locate and rectify the reference card.
///
/// Returns `found: false` with a reason rather than throwing, because "no card"
/// is an expected, first-class outcome the UI must explain to the user.
CardDetection detectCard(img.Image source, [ReferenceCardSpec? specOrNull]) {
  final ReferenceCardSpec spec = specOrNull ?? satvaCardV1;

  if (source.width < 64 || source.height < 64) {
    return const CardDetection(found: false, reason: 'image_too_small');
  }

  // Work at a reduced size: contour finding does not need full resolution, and
  // a market handset should not spend a second on it.
  final double scale = 640.0 / math.max(source.width, source.height);
  final img.Image working = scale < 1.0
      ? img.copyResize(source,
          width: (source.width * scale).round(), height: (source.height * scale).round(),)
      : source;

  final List<List<_Point>> quads = _findQuadrilaterals(working);
  if (quads.isEmpty) {
    return const CardDetection(found: false, reason: 'no_quadrilateral_found');
  }

  final double imageArea = (working.width * working.height).toDouble();
  final double inverseScale = scale < 1.0 ? 1.0 / scale : 1.0;

  CardDetection? best;
  for (final List<_Point> quad in quads) {
    final List<_Point> fullScale = quad
        .map((_Point p) => _Point(p.x * inverseScale, p.y * inverseScale))
        .toList(growable: false);

    for (int rotation = 0; rotation < 4; rotation++) {
      final List<_Point> rotated = <_Point>[
        for (int i = 0; i < 4; i++) fullScale[(i + rotation) % 4],
      ];
      final img.Image warped = _warpPerspective(source, rotated, warpWidth, warpHeight);
      final double score = _scoreOrientation(warped, spec);
      if (best == null || score > best.orientationConfidence) {
        best = CardDetection(
          found: true,
          warped: warped,
          quadAreaFraction: _polygonArea(quad) / imageArea,
          orientationConfidence: score,
        );
      }
    }
    if (best != null && best.orientationConfidence >= 0.35) break;
  }

  if (best == null) {
    return const CardDetection(found: false, reason: 'no_quadrilateral_found');
  }
  if (best.orientationConfidence < 0.18) {
    return CardDetection(
      found: false,
      reason: 'orientation_key_not_found',
      quadAreaFraction: best.quadAreaFraction,
      orientationConfidence: best.orientationConfidence,
    );
  }
  if (best.quadAreaFraction < 0.04) {
    return CardDetection(
      found: false,
      reason: 'card_too_small_in_frame',
      quadAreaFraction: best.quadAreaFraction,
    );
  }
  return best;
}

double _polygonArea(List<_Point> points) {
  double area = 0;
  for (int i = 0; i < points.length; i++) {
    final _Point a = points[i];
    final _Point b = points[(i + 1) % points.length];
    area += a.x * b.y - b.x * a.y;
  }
  return area.abs() / 2.0;
}

/// Find plausible card outlines by thresholding and walking connected regions.
List<List<_Point>> _findQuadrilaterals(img.Image source) {
  final img.Image grey = img.grayscale(source);
  final int width = grey.width;
  final int height = grey.height;

  // Otsu threshold: adapts to the frame's own histogram, which matters when
  // market lighting varies wildly between captures.
  final int threshold = _otsuThreshold(grey);
  final List<bool> dark = List<bool>.filled(width * height, false);
  for (int y = 0; y < height; y++) {
    for (int x = 0; x < width; x++) {
      dark[y * width + x] = grey.getPixel(x, y).luminance < threshold;
    }
  }

  final List<List<_Point>> results = <List<_Point>>[];
  final List<bool> visited = List<bool>.filled(width * height, false);
  final double minArea = 0.02 * width * height;

  for (int y = 0; y < height; y += 2) {
    for (int x = 0; x < width; x += 2) {
      final int index = y * width + x;
      if (visited[index] || !dark[index]) continue;

      // Flood fill the connected dark region and take its extremes.
      int minX = x, maxX = x, minY = y, maxY = y, count = 0;
      final List<int> stack = <int>[index];
      visited[index] = true;

      while (stack.isNotEmpty && count < 400000) {
        final int current = stack.removeLast();
        final int cx = current % width;
        final int cy = current ~/ width;
        count++;
        if (cx < minX) minX = cx;
        if (cx > maxX) maxX = cx;
        if (cy < minY) minY = cy;
        if (cy > maxY) maxY = cy;

        for (final List<int> delta in const <List<int>>[
          <int>[1, 0], <int>[-1, 0], <int>[0, 1], <int>[0, -1],
        ]) {
          final int nx = cx + delta[0];
          final int ny = cy + delta[1];
          if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue;
          final int neighbour = ny * width + nx;
          if (!visited[neighbour] && dark[neighbour]) {
            visited[neighbour] = true;
            stack.add(neighbour);
          }
        }
      }

      final double area = ((maxX - minX) * (maxY - minY)).toDouble();
      if (area < minArea) continue;

      final double w = (maxX - minX).toDouble();
      final double h = (maxY - minY).toDouble();
      if (h <= 1) continue;
      final double aspect = w / h;
      if (aspect < 0.70 * cardAspect || aspect > 1.45 * cardAspect) continue;

      results.add(<_Point>[
        _Point(minX.toDouble(), minY.toDouble()),
        _Point(maxX.toDouble(), minY.toDouble()),
        _Point(maxX.toDouble(), maxY.toDouble()),
        _Point(minX.toDouble(), maxY.toDouble()),
      ]);
    }
  }

  results.sort((List<_Point> a, List<_Point> b) =>
      _polygonArea(b).compareTo(_polygonArea(a)),);
  return results.take(4).toList(growable: false);
}

int _otsuThreshold(img.Image grey) {
  final List<int> histogram = List<int>.filled(256, 0);
  for (int y = 0; y < grey.height; y++) {
    for (int x = 0; x < grey.width; x++) {
      histogram[grey.getPixel(x, y).luminance.toInt().clamp(0, 255)]++;
    }
  }
  final int total = grey.width * grey.height;
  double sum = 0;
  for (int i = 0; i < 256; i++) {
    sum += i * histogram[i];
  }

  double sumB = 0;
  int weightB = 0;
  double maxVariance = 0;
  int threshold = 128;

  for (int i = 0; i < 256; i++) {
    weightB += histogram[i];
    if (weightB == 0) continue;
    final int weightF = total - weightB;
    if (weightF == 0) break;
    sumB += i * histogram[i];
    final double meanB = sumB / weightB;
    final double meanF = (sum - sumB) / weightF;
    final double variance = weightB * weightF * (meanB - meanF) * (meanB - meanF);
    if (variance > maxVariance) {
      maxVariance = variance;
      threshold = i;
    }
  }
  return threshold;
}

/// Warp a quadrilateral to the canonical card rectangle.
img.Image _warpPerspective(
  img.Image source,
  List<_Point> corners,
  int outWidth,
  int outHeight,
) {
  final img.Image out = img.Image(width: outWidth, height: outHeight);
  final _Point tl = corners[0];
  final _Point tr = corners[1];
  final _Point br = corners[2];
  final _Point bl = corners[3];

  for (int y = 0; y < outHeight; y++) {
    final double v = y / (outHeight - 1);
    for (int x = 0; x < outWidth; x++) {
      final double u = x / (outWidth - 1);

      // Bilinear interpolation between the four corners. Adequate for a card
      // lying flat; a full homography would matter only at extreme angles,
      // which the quality gate rejects anyway.
      final double topX = tl.x + (tr.x - tl.x) * u;
      final double topY = tl.y + (tr.y - tl.y) * u;
      final double bottomX = bl.x + (br.x - bl.x) * u;
      final double bottomY = bl.y + (br.y - bl.y) * u;
      final int sx = (topX + (bottomX - topX) * v).round();
      final int sy = (topY + (bottomY - topY) * v).round();

      if (sx >= 0 && sy >= 0 && sx < source.width && sy < source.height) {
        out.setPixel(x, y, source.getPixel(sx, sy));
      }
    }
  }
  return out;
}

double _scoreOrientation(img.Image warped, ReferenceCardSpec spec) {
  final List<double> rgb = samplePatch(warped, spec.orientationKey);
  final double total = rgb[0] + rgb[1] + rgb[2] + 1e-6;
  final double blueDominance =
      (rgb[2] - math.max(rgb[0], rgb[1])) / total * 3.0;
  return blueDominance.clamp(0.0, 1.0).toDouble();
}

/// Mean 8-bit RGB of a patch, sampled from its centre.
///
/// The inset discards the outer band so print registration error, homography
/// error or ink bleed at the patch edge cannot pull the mean.
List<double> samplePatch(img.Image warped, Patch patch, {double inset = 0.22}) {
  return sampleRect(
    warped,
    <double>[patch.x, patch.y, patch.w, patch.h],
    inset: inset,
  );
}

List<double> sampleRect(
  img.Image warped,
  List<double> rect, {
  double inset = 0.12,
}) {
  final int x = (rect[0] * warped.width).round();
  final int y = (rect[1] * warped.height).round();
  final int w = (rect[2] * warped.width).round();
  final int h = (rect[3] * warped.height).round();
  final int dx = (w * inset).round();
  final int dy = (h * inset).round();

  double sumR = 0, sumG = 0, sumB = 0;
  int count = 0;
  for (int py = y + dy; py < y + h - dy; py++) {
    for (int px = x + dx; px < x + w - dx; px++) {
      if (px < 0 || py < 0 || px >= warped.width || py >= warped.height) continue;
      final img.Pixel pixel = warped.getPixel(px, py);
      sumR += pixel.r;
      sumG += pixel.g;
      sumB += pixel.b;
      count++;
    }
  }
  if (count == 0) return <double>[0, 0, 0];
  return <double>[sumR / count, sumG / count, sumB / count];
}

/// All RGB pixels inside a normalised rect, flattened as [r,g,b,r,g,b,...].
List<int> sampleRectPixels(
  img.Image warped,
  List<double> rect, {
  double inset = 0.12,
}) {
  final int x = (rect[0] * warped.width).round();
  final int y = (rect[1] * warped.height).round();
  final int w = (rect[2] * warped.width).round();
  final int h = (rect[3] * warped.height).round();
  final int dx = (w * inset).round();
  final int dy = (h * inset).round();

  final List<int> out = <int>[];
  for (int py = y + dy; py < y + h - dy; py++) {
    for (int px = x + dx; px < x + w - dx; px++) {
      if (px < 0 || py < 0 || px >= warped.width || py >= warped.height) continue;
      final img.Pixel pixel = warped.getPixel(px, py);
      out..add(pixel.r.toInt())..add(pixel.g.toInt())..add(pixel.b.toInt());
    }
  }
  return out;
}
