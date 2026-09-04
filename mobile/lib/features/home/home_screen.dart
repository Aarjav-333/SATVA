/// SATVA home.
///
/// One dominant action — scan — because that is what the app is for and the
/// specification budgets ten seconds for the whole interaction. Everything else
/// is secondary and sits below it.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/storage/local_database.dart';
import '../../core/storage/scan_repository.dart';
import '../../core/theme.dart';
import '../scan/ui/capture_screen.dart';
import '../scan/vision/vision_service.dart';

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<List<LocalScan>> history = ref.watch(scanHistoryProvider);
    final AsyncValue<int> pending = ref.watch(pendingSyncCountProvider);
    final AsyncValue<VisionScreener> screener = ref.watch(visionScreenerProvider);

    return Scaffold(
      body: SafeArea(
        child: RefreshIndicator(
          onRefresh: () async {
            ref.invalidate(scanHistoryProvider);
            ref.invalidate(pendingSyncCountProvider);
          },
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
            children: <Widget>[
              const _Header(),
              const SizedBox(height: 20),

              pending.maybeWhen(
                data: (int count) =>
                    count > 0 ? _PendingSyncBanner(count: count) : const SizedBox.shrink(),
                orElse: () => const SizedBox.shrink(),
              ),

              _ScanCard(
                onTap: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const CaptureScreen()),
                ),
              ),
              const SizedBox(height: 18),

              screener.maybeWhen(
                data: (VisionScreener s) =>
                    s.isValidatedModel ? const SizedBox.shrink() : const _DevBuildNotice(),
                orElse: () => const SizedBox.shrink(),
              ),
              const SizedBox(height: 8),

              const _HowItWorks(),
              const SizedBox(height: 24),

              Text('Your scans', style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(height: 10),
              history.when(
                loading: () => const Padding(
                  padding: EdgeInsets.all(24),
                  child: Center(child: CircularProgressIndicator()),
                ),
                error: (Object e, StackTrace s) => _EmptyState(
                  icon: Icons.error_outline,
                  title: 'Could not load your history',
                  body: '$e',
                ),
                data: (List<LocalScan> scans) => scans.isEmpty
                    ? const _EmptyState(
                        icon: Icons.photo_camera_outlined,
                        title: 'No scans yet',
                        body: 'Your scans stay on this phone. Nothing is uploaded until '
                            'you choose to share a confirmed reading.',
                      )
                    : Column(
                        children: <Widget>[
                          for (final LocalScan scan in scans.take(12)) _ScanTile(scan: scan),
                        ],
                      ),
              ),

              const SizedBox(height: 28),
              const _Disclaimer(),
            ],
          ),
        ),
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header();

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              const Text(
                'SATVA',
                style: TextStyle(
                  fontSize: 30,
                  fontWeight: FontWeight.w800,
                  color: SatvaColors.brand,
                  letterSpacing: 2.5,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                'Check your produce before you buy it',
                style: Theme.of(context).textTheme.bodyMedium,
              ),
            ],
          ),
        ),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: SatvaColors.brandSoft,
            borderRadius: BorderRadius.circular(20),
          ),
          child: const Text(
            'FREE',
            style: TextStyle(
              fontSize: 11.5,
              fontWeight: FontWeight.w800,
              color: SatvaColors.brandDark,
              letterSpacing: 0.8,
            ),
          ),
        ),
      ],
    );
  }
}

class _ScanCard extends StatelessWidget {
  const _ScanCard({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: SatvaColors.brand,
      borderRadius: BorderRadius.circular(20),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(20),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 26),
          child: Row(
            children: <Widget>[
              Container(
                width: 56,
                height: 56,
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.16),
                  borderRadius: BorderRadius.circular(16),
                ),
                child: const Icon(Icons.photo_camera_rounded, color: Colors.white, size: 30),
              ),
              const SizedBox(width: 18),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    const Text(
                      'Scan produce',
                      style: TextStyle(
                        fontSize: 21,
                        fontWeight: FontWeight.w700,
                        color: Colors.white,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      'Works offline · about 10 seconds',
                      style: TextStyle(
                        fontSize: 13.5,
                        color: Colors.white.withValues(alpha: 0.88),
                      ),
                    ),
                  ],
                ),
              ),
              Icon(Icons.arrow_forward_rounded,
                  color: Colors.white.withValues(alpha: 0.9),),
            ],
          ),
        ),
      ),
    );
  }
}

class _PendingSyncBanner extends ConsumerWidget {
  const _PendingSyncBanner({required this.count});

  final int count;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Container(
      margin: const EdgeInsets.only(bottom: 14),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: SatvaColors.unsureSoft,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: SatvaColors.unsure.withValues(alpha: 0.25)),
      ),
      child: Row(
        children: <Widget>[
          const Icon(Icons.sync_outlined, size: 20, color: SatvaColors.unsure),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              '$count ${count == 1 ? 'scan is' : 'scans are'} waiting to sync. They are '
              'saved safely on this phone.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
          TextButton(
            onPressed: () async {
              final sync = await ref.read(syncServiceProvider.future);
              await sync.syncNow();
              ref.invalidate(pendingSyncCountProvider);
              ref.invalidate(scanHistoryProvider);
            },
            child: const Text('Sync'),
          ),
        ],
      ),
    );
  }
}

class _HowItWorks extends StatelessWidget {
  const _HowItWorks();

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('Two steps, on purpose', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 12),
            const _Step(
              number: '1',
              title: 'Photograph the produce',
              body: 'A model on this phone looks for visual signs of forced ripening. '
                  'Free, instant, and it never leaves your phone.',
            ),
            const SizedBox(height: 12),
            const _Step(
              number: '2',
              title: 'Confirm with a ₹5 strip test',
              body: 'Only if step 1 is suspicious. The strip is photographed against a '
                  'reference card and read as a real measurement. This is the only '
                  'result SATVA will ever act on.',
            ),
          ],
        ),
      ),
    );
  }
}

class _Step extends StatelessWidget {
  const _Step({required this.number, required this.title, required this.body});

  final String number;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Container(
          width: 26,
          height: 26,
          alignment: Alignment.center,
          decoration: const BoxDecoration(
            color: SatvaColors.brandSoft,
            shape: BoxShape.circle,
          ),
          child: Text(
            number,
            style: const TextStyle(
              fontWeight: FontWeight.w800,
              color: SatvaColors.brandDark,
              fontSize: 13,
            ),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(title, style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 2),
              Text(body, style: Theme.of(context).textTheme.bodySmall),
            ],
          ),
        ),
      ],
    );
  }
}

class _ScanTile extends StatelessWidget {
  const _ScanTile({required this.scan});

  final LocalScan scan;

  @override
  Widget build(BuildContext context) {
    final ResultPalette palette = switch (scan.evidenceGrade) {
      EvidenceGrade.confirmatory => scan.reading?.exceedsActionThreshold ?? false
          ? ResultPalette.confirmed
          : ResultPalette.clear,
      EvidenceGrade.rejected => ResultPalette.refused,
      _ => (scan.visionVerdict == 'suspicious')
          ? ResultPalette.caution
          : (scan.visionVerdict == 'inconclusive')
              ? ResultPalette.unsure
              : ResultPalette.clear,
    };

    final String subtitle = switch (scan.evidenceGrade) {
      EvidenceGrade.confirmatory =>
        '${scan.reading?.concentrationValue?.toStringAsFixed(2) ?? '—'} '
            '${scan.reading?.concentrationUnit ?? ''}',
      EvidenceGrade.rejected => 'Measurement refused',
      _ => 'Screened only · score ${scan.anomalyScore?.toStringAsFixed(0) ?? '—'}',
    };

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: SatvaColors.surface,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: SatvaColors.line),
      ),
      child: Row(
        children: <Widget>[
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(
              color: palette.background,
              borderRadius: BorderRadius.circular(11),
            ),
            child: Icon(palette.icon, size: 21, color: palette.foreground),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  CropCatalogue.supported[scan.crop] ?? scan.crop,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 1),
                Text(subtitle, style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
          ),
          if (scan.syncStatus == SyncStatus.pending)
            const Icon(Icons.cloud_upload_outlined, size: 17, color: SatvaColors.inkFaint)
          else if (scan.syncStatus == SyncStatus.synced)
            const Icon(Icons.cloud_done_outlined, size: 17, color: SatvaColors.inkFaint),
        ],
      ),
    );
  }
}

class _DevBuildNotice extends StatelessWidget {
  const _DevBuildNotice();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: SatvaColors.cautionSoft,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: SatvaColors.caution.withValues(alpha: 0.28)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Icon(Icons.construction_outlined, size: 19, color: SatvaColors.caution),
          const SizedBox(width: 11),
          Expanded(
            child: Text(
              'Demonstration build. The screening model has not been validated on real '
              'produce, so scores here are for showing how SATVA works — not for '
              'judging actual food.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        ],
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.icon, required this.title, required this.body});

  final IconData icon;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 32),
      decoration: BoxDecoration(
        color: SatvaColors.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: SatvaColors.line),
      ),
      child: Column(
        children: <Widget>[
          Icon(icon, size: 30, color: SatvaColors.inkFaint),
          const SizedBox(height: 12),
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 6),
          Text(
            body,
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

class _Disclaimer extends StatelessWidget {
  const _Disclaimer();

  @override
  Widget build(BuildContext context) {
    return Text(
      Disclaimers.long,
      style: Theme.of(context).textTheme.bodySmall?.copyWith(fontSize: 11.5),
      textAlign: TextAlign.center,
    );
  }
}
