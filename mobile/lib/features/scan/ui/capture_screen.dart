/// The Scan flow: choose produce, capture, screen, and (if warranted) confirm.
///
/// Everything here runs on the handset. No step waits on the network, and the
/// scan is durable in SQLite before any sync is attempted.
library;

import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';
import 'package:image/image.dart' as img;
import 'package:image_picker/image_picker.dart';

import '../../../core/providers.dart';
import '../../../core/storage/scan_repository.dart';
import '../../../core/theme.dart';
import '../../colorimetry/engine/pipeline.dart';
import '../../colorimetry/ui/strip_capture_screen.dart';
import '../../colorimetry/ui/strip_result_screen.dart';
import '../vision/vision_service.dart';
import 'result_screen.dart';

class CaptureScreen extends ConsumerStatefulWidget {
  const CaptureScreen({super.key});

  @override
  ConsumerState<CaptureScreen> createState() => _CaptureScreenState();
}

class _CaptureScreenState extends ConsumerState<CaptureScreen> {
  final ImagePicker _picker = ImagePicker();

  String _crop = 'mango';
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
        // 1600px is enough for both screening and the strip read, and keeps
        // decode time down on a low-end handset.
        maxWidth: 1600,
        maxHeight: 1600,
        imageQuality: 92,
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

      final VisionScreener screener = await ref.read(visionScreenerProvider.future);
      final VisionResult result = await screener.screen(decoded, crop: _crop);

      // Location is best-effort and never blocks. A scan with no fix is still a
      // perfectly good scan; it simply cannot contribute to a hotspot.
      final Position? position = await _tryLocation();

      final ScanRepository repository = await ref.read(scanRepositoryProvider.future);
      final String uid = await repository.saveScreening(
        crop: _crop,
        vision: result,
        latitude: position?.latitude,
        longitude: position?.longitude,
        locationAccuracyM: position?.accuracy,
        perceptualHash: _perceptualHash(decoded),
      );

      if (!mounted) return;
      setState(() => _busy = false);

      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => ScreeningResultScreen(
            result: result,
            crop: _crop,
            imageBytes: bytes,
            onStartStripTest: () => _startStripTest(uid),
            onSaveOnly: () {
              ref.invalidate(scanHistoryProvider);
              ref.invalidate(pendingSyncCountProvider);
              Navigator.of(context)..pop()..pop();
            },
            onDiscard: () => Navigator.of(context).pop(),
          ),
        ),
      );
      ref.invalidate(scanHistoryProvider);
      ref.invalidate(pendingSyncCountProvider);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = e.toString();
      });
    }
  }

  Future<void> _startStripTest(String scanUid) async {
    final ColorimetryResult? result = await Navigator.of(context).push<ColorimetryResult>(
      MaterialPageRoute<ColorimetryResult>(
        builder: (_) => StripCaptureScreen(crop: _crop),
      ),
    );
    if (result == null || !mounted) return;

    final ScanRepository repository = await ref.read(scanRepositoryProvider.future);
    await repository.attachReading(scanUid, result);
    ref.invalidate(scanHistoryProvider);
    ref.invalidate(pendingSyncCountProvider);

    if (!mounted) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => StripResultScreen(
          result: result,
          crop: _crop,
          onRetake: () {
            Navigator.of(context).pop();
            _startStripTest(scanUid);
          },
          onSave: () => Navigator.of(context)..pop()..pop()..pop(),
          onShareWithWatch: () async {
            try {
              await repository.setSharedWithWatch(scanUid, true);
              if (!mounted) return;
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(
                  content: Text(
                    'Shared anonymously. It will appear on the ward map once other '
                    'devices corroborate it.',
                  ),
                ),
              );
            } on StateError catch (e) {
              if (!mounted) return;
              ScaffoldMessenger.of(context)
                  .showSnackBar(SnackBar(content: Text(e.message)));
            }
          },
          onGenerateComplaint: () {
            ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(
                content: Text(
                  'Complaint packages are prepared once this reading syncs. '
                  'SATVA never files on your behalf.',
                ),
              ),
            );
          },
        ),
      ),
    );
  }

  Future<Position?> _tryLocation() async {
    try {
      LocationPermission permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied ||
          permission == LocationPermission.deniedForever) {
        return null;
      }
      return await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.high,
          timeLimit: Duration(seconds: 6),
        ),
      );
    } catch (_) {
      // No fix, no permission, or timed out. All fine.
      return null;
    }
  }

  /// Perceptual hash for duplicate detection (specification §3.3).
  ///
  /// A 64-bit average hash over an 8x8 greyscale reduction. Computed on-device
  /// so the server can collapse repeat submissions without the original image
  /// ever being uploaded for a screening-only scan.
  String _perceptualHash(img.Image source) {
    final img.Image small =
        img.copyResize(img.grayscale(source), width: 8, height: 8);
    int sum = 0;
    final List<int> values = <int>[];
    for (int y = 0; y < 8; y++) {
      for (int x = 0; x < 8; x++) {
        final int v = small.getPixel(x, y).luminance.toInt();
        values.add(v);
        sum += v;
      }
    }
    final double mean = sum / values.length;
    int hash = 0;
    for (final int v in values) {
      hash = (hash << 1) | (v > mean ? 1 : 0);
    }
    return hash.toUnsigned(64).toRadixString(16).padLeft(16, '0');
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Scan produce')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
        children: <Widget>[
          Text('What are you checking?', style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: <Widget>[
              for (final MapEntry<String, String> entry
                  in CropCatalogue.supported.entries)
                ChoiceChip(
                  label: Text(entry.value),
                  selected: _crop == entry.key,
                  onSelected: (_) => setState(() => _crop = entry.key),
                ),
            ],
          ),
          const SizedBox(height: 18),
          const _UnsupportedCropsNote(),
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
              child: Center(child: CircularProgressIndicator()),
            )
          else ...<Widget>[
            FilledButton.icon(
              onPressed: () => _capture(ImageSource.camera),
              icon: const Icon(Icons.photo_camera_outlined),
              label: const Text('Take a photo'),
            ),
            const SizedBox(height: 10),
            OutlinedButton.icon(
              onPressed: () => _capture(ImageSource.gallery),
              icon: const Icon(Icons.photo_library_outlined),
              label: const Text('Choose from gallery'),
            ),
          ],

          const SizedBox(height: 20),
          Text(
            'The photo is analysed on this phone and is not uploaded. Only a confirmed '
            'strip reading is ever shared, and only if you choose to share it.',
            style: Theme.of(context).textTheme.bodySmall,
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
}

/// Names the crops SATVA will not score, and why.
///
/// Specification rule 15 requires refusal rather than guessing. Listing the
/// refused crops up front is more honest and more useful than letting somebody
/// discover the refusal after they have taken the photo.
class _UnsupportedCropsNote extends StatelessWidget {
  const _UnsupportedCropsNote();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(15),
      decoration: BoxDecoration(
        color: SatvaColors.refusedSoft,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: SatvaColors.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              const Icon(Icons.block_outlined, size: 17, color: SatvaColors.inkSoft),
              const SizedBox(width: 8),
              Text(
                'Not yet available for',
                style: Theme.of(context).textTheme.titleMedium?.copyWith(fontSize: 14.5),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            CropCatalogue.refused.values.join(' · '),
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 6),
          Text(
            CropCatalogue.refusalNote,
            style: Theme.of(context).textTheme.bodySmall?.copyWith(fontSize: 11.5),
          ),
        ],
      ),
    );
  }
}
