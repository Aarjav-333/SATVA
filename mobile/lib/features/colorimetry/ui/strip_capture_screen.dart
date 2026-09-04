/// Layer B capture: photograph the reacted strip beside the reference card.
///
/// The guidance on this screen is not decoration. Most refusals are caused by
/// how the photo was taken, so telling people what "good" looks like *before*
/// they shoot is the cheapest way to raise the acceptance rate — and the whole
/// pipeline is designed to refuse rather than guess when they get it wrong.
library;

import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image/image.dart' as img;
import 'package:image_picker/image_picker.dart';

import '../../../core/providers.dart';
import '../../../core/theme.dart';
import '../engine/calibration.dart';
import '../engine/pipeline.dart';

class StripCaptureScreen extends ConsumerStatefulWidget {
  const StripCaptureScreen({super.key, required this.crop});

  final String crop;

  @override
  ConsumerState<StripCaptureScreen> createState() => _StripCaptureScreenState();
}

class _StripCaptureScreenState extends ConsumerState<StripCaptureScreen> {
  final ImagePicker _picker = ImagePicker();
  String _assay = 'turmeric_carbide';
  bool _busy = false;
  String? _error;

  Future<void> _capture(ImageSource source) async {
    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      final XFile? file = await _picker.pickImage(
        source: source,
        maxWidth: 2000,
        maxHeight: 2000,
        // Quality 95: JPEG artefacts are a genuine source of colour error, and
        // this is a measurement, not a snapshot.
        imageQuality: 95,
      );
      if (file == null) {
        setState(() => _busy = false);
        return;
      }

      final Uint8List bytes = await file.readAsBytes();
      final img.Image? decoded = img.decodeImage(bytes);
      if (decoded == null) {
        throw const FormatException('That photo could not be read.');
      }

      // Ensure the calibration tables are loaded before reading.
      await ref.read(calibrationProvider.future);
      final ColorimetryResult result = readStrip(decoded, _assay);

      if (!mounted) return;
      Navigator.of(context).pop(result);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = e.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final AsyncValue<CalibrationRegistry> registry = ref.watch(calibrationProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Strip test')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
        children: <Widget>[
          Text('Which test?', style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 10),
          registry.when(
            loading: () => const LinearProgressIndicator(),
            error: (Object e, StackTrace s) => Text('Calibrations unavailable: $e'),
            data: (CalibrationRegistry reg) {
              final List<CalibrationSeries> series = reg.all
                  .where((CalibrationSeries s) =>
                      s.supportedCrops.isEmpty ||
                      s.supportedCrops.contains(widget.crop),)
                  .toList();
              return RadioGroup<String>(
                groupValue: _assay,
                onChanged: (String? v) => setState(() => _assay = v ?? _assay),
                child: Column(
                  children: <Widget>[
                    for (final CalibrationSeries s in series)
                      RadioListTile<String>(
                        value: s.assay,
                        title: Text(s.displayName),
                        subtitle: Text('Reported in ${s.unit}'),
                        contentPadding: EdgeInsets.zero,
                      ),
                  ],
                ),
              );
            },
          ),
          const SizedBox(height: 18),

          const _CaptureGuidance(),
          const SizedBox(height: 24),

          if (_error != null) ...<Widget>[
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: SatvaColors.cautionSoft,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: SatvaColors.caution.withValues(alpha: 0.3)),
              ),
              child: Text(_error!, style: Theme.of(context).textTheme.bodySmall),
            ),
            const SizedBox(height: 16),
          ],

          if (_busy)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 30),
              child: Center(
                child: Column(
                  children: <Widget>[
                    CircularProgressIndicator(),
                    SizedBox(height: 14),
                    Text('Reading the strip…'),
                  ],
                ),
              ),
            )
          else ...<Widget>[
            FilledButton.icon(
              onPressed: () => _capture(ImageSource.camera),
              icon: const Icon(Icons.photo_camera_outlined),
              label: const Text('Photograph strip and card'),
            ),
            const SizedBox(height: 10),
            OutlinedButton.icon(
              onPressed: () => _capture(ImageSource.gallery),
              icon: const Icon(Icons.photo_library_outlined),
              label: const Text('Choose from gallery'),
            ),
          ],
        ],
      ),
    );
  }
}

class _CaptureGuidance extends StatelessWidget {
  const _CaptureGuidance();

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              'For a reading SATVA will accept',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 12),
            const _Tip(Icons.crop_free, 'The whole reference card must be in the frame'),
            const _Tip(Icons.wb_sunny_outlined, 'Even light — not direct sun, not deep shade'),
            const _Tip(Icons.pan_tool_outlined, 'No shadow falling across the card'),
            const _Tip(Icons.straighten, 'Hold the phone square to the card, about 20 cm away'),
            const _Tip(Icons.grid_4x4, 'Lay the strip flat inside the marked window'),
            const SizedBox(height: 14),
            Container(
              padding: const EdgeInsets.all(13),
              decoration: BoxDecoration(
                color: SatvaColors.brandSoft,
                borderRadius: BorderRadius.circular(11),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  const Icon(Icons.info_outline, size: 17, color: SatvaColors.brandDark),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      'If the photo is not good enough, SATVA will say so and ask you to '
                      'retake it rather than reporting an unreliable number.',
                      style: Theme.of(context)
                          .textTheme
                          .bodySmall
                          ?.copyWith(color: SatvaColors.brandDark),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Tip extends StatelessWidget {
  const _Tip(this.icon, this.text);

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(icon, size: 18, color: SatvaColors.brand),
          const SizedBox(width: 11),
          Expanded(child: Text(text, style: Theme.of(context).textTheme.bodyMedium)),
        ],
      ),
    );
  }
}
