/// Saliency overlay for the screening result.
///
/// Draws the regions the screening responded to over the captured photograph.
///
/// The label under the overlay is deliberate. For the TFLite path these are the
/// anatomical regions the model was trained to attend to, weighted by the score
/// — not a per-pixel attention map, which would need gradient access or ~25
/// extra forward passes and would blow the ten-second interaction budget. For
/// the heuristic path they are the regions whose indicators actually fired.
/// Presenting either as a true attention map would overstate what is shown, so
/// the UI says "regions this result responded to" instead.
library;

import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../vision/vision_service.dart';

class SaliencyOverlay extends StatelessWidget {
  const SaliencyOverlay({
    super.key,
    required this.imageBytes,
    required this.regions,
    this.showOverlay = true,
  });

  final List<int> imageBytes;
  final List<SaliencyRegion> regions;
  final bool showOverlay;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        ClipRRect(
          borderRadius: BorderRadius.circular(16),
          child: AspectRatio(
            aspectRatio: 4 / 3,
            child: Stack(
              fit: StackFit.expand,
              children: <Widget>[
                Image.memory(
                  Uint8List.fromList(imageBytes),
                  fit: BoxFit.cover,
                  gaplessPlayback: true,
                ),
                if (showOverlay && regions.isNotEmpty)
                  CustomPaint(painter: _SaliencyPainter(regions)),
              ],
            ),
          ),
        ),
        if (showOverlay && regions.isNotEmpty) ...<Widget>[
          const SizedBox(height: 10),
          Text(
            'Regions this result responded to',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 6),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: <Widget>[
              for (final SaliencyRegion region in regions)
                if (region.label != null)
                  Chip(
                    visualDensity: VisualDensity.compact,
                    avatar: Icon(
                      Icons.circle,
                      size: 10,
                      color: SatvaColors.caution.withValues(alpha: 0.35 + region.weight * 0.6),
                    ),
                    label: Text(region.label!),
                  ),
            ],
          ),
        ],
      ],
    );
  }
}

class _SaliencyPainter extends CustomPainter {
  _SaliencyPainter(this.regions);

  final List<SaliencyRegion> regions;

  @override
  void paint(Canvas canvas, Size size) {
    for (final SaliencyRegion region in regions) {
      final Rect rect = Rect.fromLTWH(
        region.x * size.width,
        region.y * size.height,
        region.width * size.width,
        region.height * size.height,
      );
      final RRect rounded = RRect.fromRectAndRadius(rect, const Radius.circular(10));

      // Soft fill, weighted by the indicator strength, plus a clear outline.
      // A heat blob alone reads as a claim about pixels; an outline reads as
      // "we looked here", which is what is actually true.
      canvas.drawRRect(
        rounded,
        Paint()
          ..color = SatvaColors.caution.withValues(alpha: 0.10 + region.weight * 0.22)
          ..style = PaintingStyle.fill,
      );
      canvas.drawRRect(
        rounded,
        Paint()
          ..color = SatvaColors.caution.withValues(alpha: 0.55 + region.weight * 0.4)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2.5,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _SaliencyPainter oldDelegate) =>
      oldDelegate.regions != regions;
}
