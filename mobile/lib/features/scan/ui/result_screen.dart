/// The screening result screen — the most important screen in SATVA.
///
/// Everything about it is shaped by one rule: **a photograph must never
/// condemn a vendor.** So the screen:
///
/// * leads with what the user should *do*, not with a verdict;
/// * states in plain words that this is a screening step, not proof;
/// * shows the score as supporting detail, not as a headline number;
/// * disables every escalation control until a strip reading exists, and says
///   why rather than just greying a button out;
/// * uses amber, never red, for a suspicious visual result.
library;

import 'package:flutter/material.dart';

import '../../../core/providers.dart';
import '../../../core/storage/scan_repository.dart';
import '../../../core/theme.dart';
import '../vision/vision_service.dart';
import 'saliency_overlay.dart';

class ScreeningResultScreen extends StatelessWidget {
  const ScreeningResultScreen({
    super.key,
    required this.result,
    required this.crop,
    required this.imageBytes,
    required this.onStartStripTest,
    required this.onDiscard,
    required this.onSaveOnly,
    this.reading,
  });

  final VisionResult result;
  final String crop;
  final List<int>? imageBytes;
  final VoidCallback onStartStripTest;
  final VoidCallback onDiscard;
  final VoidCallback onSaveOnly;
  final LocalReading? reading;

  bool get _refusedCrop => result.verdict == ScreeningVerdict.refusedUnsupportedCrop;

  ResultPalette get _palette {
    if (_refusedCrop) return ResultPalette.refused;
    return switch (result.verdict) {
      ScreeningVerdict.suspicious => ResultPalette.caution,
      ScreeningVerdict.inconclusive => ResultPalette.unsure,
      ScreeningVerdict.notSuspicious => ResultPalette.clear,
      _ => ResultPalette.refused,
    };
  }

  /// The headline is an *action*, not a verdict. "Do the strip test" is
  /// actionable and honest; "Adulterated" would be neither.
  String get _headline {
    if (_refusedCrop) return 'SATVA will not score this';
    return switch (result.verdict) {
      ScreeningVerdict.suspicious => 'Worth doing the strip test',
      ScreeningVerdict.inconclusive => 'Not sure — the strip test would settle it',
      ScreeningVerdict.notSuspicious => 'Nothing unusual in the picture',
      _ => 'Could not screen this photo',
    };
  }

  String get _explanation {
    if (_refusedCrop) {
      return result.refusalReason ?? CropCatalogue.refusalNote;
    }
    return switch (result.verdict) {
      ScreeningVerdict.suspicious =>
        'The photo shows visual signs associated with forced ripening. That is not '
            'proof of anything — it only means a chemical test is worth the ₹5.',
      ScreeningVerdict.inconclusive =>
        'The photo is neither clearly normal nor clearly unusual. SATVA would rather '
            'say so than guess.',
      ScreeningVerdict.notSuspicious =>
        'The photo does not show the visual signs associated with forced ripening. '
            'This is not a guarantee that the produce is safe — a photograph cannot '
            'detect a chemical.',
      _ => 'The photo could not be screened.',
    };
  }

  @override
  Widget build(BuildContext context) {
    final ResultPalette palette = _palette;
    final TextTheme text = Theme.of(context).textTheme;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Screening result'),
        leading: IconButton(
          icon: const Icon(Icons.close),
          onPressed: onDiscard,
          tooltip: 'Discard',
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
        children: <Widget>[
          _ResultBanner(palette: palette, headline: _headline, explanation: _explanation),
          const SizedBox(height: 20),

          if (imageBytes != null && !_refusedCrop) ...<Widget>[
            SaliencyOverlay(
              imageBytes: imageBytes!,
              regions: result.saliency,
              showOverlay: result.verdict != ScreeningVerdict.notSuspicious,
            ),
            const SizedBox(height: 20),
          ],

          if (!_refusedCrop) _ScoreDetail(result: result, crop: crop),
          const SizedBox(height: 16),

          const _EvidenceRuleCard(),
          const SizedBox(height: 12),

          if (!result.isValidatedModel) const _DevModelWarning(),
          const SizedBox(height: 24),

          if (!_refusedCrop && result.suggestsStripTest) ...<Widget>[
            FilledButton.icon(
              onPressed: onStartStripTest,
              icon: const Icon(Icons.science_outlined),
              label: const Text('Do the strip test'),
            ),
            const SizedBox(height: 10),
            Text(
              'You will photograph the reacted strip next to the SATVA reference card. '
              'This is the only step that produces a result SATVA will act on.',
              style: text.bodySmall,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 14),
            OutlinedButton(onPressed: onSaveOnly, child: const Text('Just save this scan')),
          ] else if (!_refusedCrop) ...<Widget>[
            FilledButton(onPressed: onSaveOnly, child: const Text('Save to my history')),
            const SizedBox(height: 10),
            OutlinedButton.icon(
              onPressed: onStartStripTest,
              icon: const Icon(Icons.science_outlined),
              label: const Text('Do the strip test anyway'),
            ),
          ] else
            FilledButton(onPressed: onDiscard, child: const Text('Go back')),
        ],
      ),
    );
  }
}

class _ResultBanner extends StatelessWidget {
  const _ResultBanner({
    required this.palette,
    required this.headline,
    required this.explanation,
  });

  final ResultPalette palette;
  final String headline;
  final String explanation;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: palette.background,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: palette.foreground.withValues(alpha: 0.24)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Icon(palette.icon, color: palette.foreground, size: 30),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  headline,
                  style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                        color: palette.foreground,
                      ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            explanation,
            style: Theme.of(context)
                .textTheme
                .bodyLarge
                ?.copyWith(color: SatvaColors.ink.withValues(alpha: 0.86)),
          ),
        ],
      ),
    );
  }
}

class _ScoreDetail extends StatelessWidget {
  const _ScoreDetail({required this.result, required this.crop});

  final VisionResult result;
  final String crop;

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              crossAxisAlignment: CrossAxisAlignment.end,
              children: <Widget>[
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text('Visual screening score', style: text.bodySmall),
                    const SizedBox(height: 2),
                    Text(
                      result.anomalyScore.toStringAsFixed(0),
                      style: text.displaySmall,
                    ),
                  ],
                ),
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text('out of 100', style: text.bodyMedium),
                ),
              ],
            ),
            const SizedBox(height: 14),
            _ScoreBar(score: result.anomalyScore),
            const SizedBox(height: 16),
            const Divider(),
            const SizedBox(height: 12),
            _DetailRow(label: 'Produce', value: CropCatalogue.supported[crop] ?? crop),
            if (result.ripenessIndex != null)
              _DetailRow(
                label: 'Ripeness',
                value: '${(result.ripenessIndex! * 100).toStringAsFixed(0)}%',
              ),
            if (result.inferenceMs != null)
              _DetailRow(label: 'Ran on this phone in', value: '${result.inferenceMs} ms'),
            _DetailRow(
              label: 'Screening method',
              value: result.modelKind == 'dev_heuristic'
                  ? 'Image analysis (no trained model)'
                  : result.modelId,
            ),
          ],
        ),
      ),
    );
  }
}

/// A band scale rather than a bare number.
///
/// A lone "78/100" invites the reading "78% adulterated", which is not what it
/// means at all. Showing the three triage bands makes the number's actual job
/// visible: it selects a band, and the band selects an action.
class _ScoreBar extends StatelessWidget {
  const _ScoreBar({required this.score});

  final double score;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: <Widget>[
        LayoutBuilder(
          builder: (BuildContext context, BoxConstraints constraints) {
            final double width = constraints.maxWidth;
            return SizedBox(
              height: 30,
              child: Stack(
                children: <Widget>[
                  const Positioned.fill(
                    top: 10,
                    child: Row(
                      children: <Widget>[
                        Expanded(flex: 35, child: _BandSegment(SatvaColors.clear, true, false)),
                        Expanded(flex: 25, child: _BandSegment(SatvaColors.unsure, false, false)),
                        Expanded(flex: 40, child: _BandSegment(SatvaColors.caution, false, true)),
                      ],
                    ),
                  ),
                  Positioned(
                    left: (width * (score / 100)).clamp(0.0, width - 3),
                    child: Container(
                      width: 3,
                      height: 26,
                      decoration: BoxDecoration(
                        color: SatvaColors.ink,
                        borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                  ),
                ],
              ),
            );
          },
        ),
        const SizedBox(height: 6),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: <Widget>[
            Text('Nothing unusual', style: Theme.of(context).textTheme.bodySmall),
            Text('Not sure', style: Theme.of(context).textTheme.bodySmall),
            Text('Worth testing', style: Theme.of(context).textTheme.bodySmall),
          ],
        ),
      ],
    );
  }
}

class _BandSegment extends StatelessWidget {
  const _BandSegment(this.color, this.first, this.last);

  final Color color;
  final bool first;
  final bool last;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 10,
      margin: const EdgeInsets.symmetric(horizontal: 1),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.30),
        borderRadius: BorderRadius.horizontal(
          left: Radius.circular(first ? 6 : 0),
          right: Radius.circular(last ? 6 : 0),
        ),
      ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  const _DetailRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Expanded(
            flex: 4,
            child: Text(label, style: Theme.of(context).textTheme.bodyMedium),
          ),
          Expanded(
            flex: 5,
            child: Text(
              value,
              textAlign: TextAlign.right,
              style: Theme.of(context)
                  .textTheme
                  .bodyMedium
                  ?.copyWith(color: SatvaColors.ink, fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
    );
  }
}

/// The non-negotiable rule, stated on the screen where it matters most.
class _EvidenceRuleCard extends StatelessWidget {
  const _EvidenceRuleCard();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: SatvaColors.brandSoft,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: SatvaColors.brand.withValues(alpha: 0.22)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Icon(Icons.shield_outlined, size: 20, color: SatvaColors.brand),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  'A photo never convicts anyone',
                  style: Theme.of(context)
                      .textTheme
                      .titleMedium
                      ?.copyWith(color: SatvaColors.brandDark),
                ),
                const SizedBox(height: 4),
                Text(
                  Disclaimers.photoNeverConvicts,
                  style: Theme.of(context)
                      .textTheme
                      .bodySmall
                      ?.copyWith(color: SatvaColors.brandDark),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _DevModelWarning extends StatelessWidget {
  const _DevModelWarning();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: SatvaColors.refusedSoft,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: SatvaColors.line),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Icon(Icons.construction_outlined, size: 18, color: SatvaColors.inkSoft),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              'Development model. This build has not been validated on real produce, '
              'so this score is for demonstration only.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        ],
      ),
    );
  }
}
