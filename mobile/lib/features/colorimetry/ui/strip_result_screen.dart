/// Layer B result — the only screen in SATVA that shows evidence.
///
/// It has two entirely different faces, and that is the point:
///
/// * **Accepted** — a number, its confidence interval, and what the user can do
///   with it. The interval is shown as prominently as the value, because a
///   number without its uncertainty invites over-reading.
/// * **Refused** — no number at all, an explanation of what went wrong, and a
///   way to retake the photo. Non-negotiable rule 3: bad capture conditions
///   cause refusal, not a weak result. A refusal screen that looked like a
///   failure would push people to keep retrying until they got a number, which
///   is exactly the behaviour the rule exists to prevent.
library;

import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../engine/pipeline.dart';

class StripResultScreen extends StatelessWidget {
  const StripResultScreen({
    super.key,
    required this.result,
    required this.crop,
    required this.onRetake,
    required this.onSave,
    required this.onShareWithWatch,
    required this.onGenerateComplaint,
    this.isOffline = false,
  });

  final ColorimetryResult result;
  final String crop;
  final VoidCallback onRetake;
  final VoidCallback onSave;
  final VoidCallback onShareWithWatch;
  final VoidCallback onGenerateComplaint;
  final bool isOffline;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Strip test result')),
      body: result.accepted ? _AcceptedBody(this) : _RefusedBody(this),
    );
  }
}

class _AcceptedBody extends StatelessWidget {
  const _AcceptedBody(this.parent);

  final StripResultScreen parent;

  @override
  Widget build(BuildContext context) {
    final ColorimetryResult result = parent.result;
    final TextTheme text = Theme.of(context).textTheme;
    final bool exceeds = result.exceedsActionThreshold;
    final ResultPalette palette =
        exceeds ? ResultPalette.confirmed : ResultPalette.clear;

    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
      children: <Widget>[
        Container(
          padding: const EdgeInsets.all(22),
          decoration: BoxDecoration(
            color: palette.background,
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: palette.foreground.withValues(alpha: 0.25)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Icon(palette.icon, color: palette.foreground, size: 26),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      exceeds
                          ? 'Reading is above the action level'
                          : 'Reading is below the action level',
                      style: text.titleLarge?.copyWith(color: palette.foreground),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 18),
              Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: <Widget>[
                  Text(
                    result.concentration!.toStringAsFixed(2),
                    style: text.displaySmall?.copyWith(color: palette.foreground),
                  ),
                  const SizedBox(width: 8),
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Text(result.unit ?? '', style: text.bodyMedium),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              // The interval is given the same visual weight as the value.
              // A point estimate shown alone reads as exact, which it is not.
              Text(
                'Between ${result.ciLow!.toStringAsFixed(2)} and '
                '${result.ciHigh!.toStringAsFixed(2)}',
                style: text.bodyLarge?.copyWith(fontWeight: FontWeight.w600),
              ),
              if (result.bandLabel != null) ...<Widget>[
                const SizedBox(height: 10),
                Chip(label: Text(result.bandLabel!.toUpperCase())),
              ],
              if (result.atRangeLimit) ...<Widget>[
                const SizedBox(height: 10),
                Text(
                  result.rangeLimitSide == 'upper'
                      ? 'At or above the highest calibrated level — the true value may be '
                          'higher. SATVA does not extrapolate.'
                      : 'At or below the lowest calibrated level.',
                  style: text.bodySmall,
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 18),

        if (!result.isLabValidated) const _ProvisionalCalibrationCard(),
        const SizedBox(height: 12),

        _MeasurementQualityCard(result: result),
        const SizedBox(height: 20),

        Text('What you can do', style: text.titleLarge),
        const SizedBox(height: 12),

        FilledButton.icon(
          onPressed: parent.onSave,
          icon: const Icon(Icons.save_outlined),
          label: const Text('Save this reading'),
        ),
        const SizedBox(height: 10),
        OutlinedButton.icon(
          onPressed: parent.onShareWithWatch,
          icon: const Icon(Icons.public_outlined),
          label: const Text('Contribute anonymously to SATVA Watch'),
        ),
        const SizedBox(height: 6),
        Text(
          'Your reading joins a ward-level map. No vendor is ever named publicly, and '
          'your identity is never attached to the reading.',
          style: text.bodySmall,
        ),
        const SizedBox(height: 14),
        OutlinedButton.icon(
          onPressed: exceeds ? parent.onGenerateComplaint : null,
          icon: const Icon(Icons.description_outlined),
          label: const Text('Prepare an FSSAI complaint'),
        ),
        const SizedBox(height: 6),
        Text(
          exceeds
              ? 'SATVA prepares the evidence package. You file it yourself through the '
                  'official FSSAI channel — SATVA never submits on your behalf.'
              : 'A complaint package needs a reading above the action level. This one is '
                  'below it.',
          style: text.bodySmall,
        ),

        if (parent.isOffline) ...<Widget>[
          const SizedBox(height: 18),
          const _OfflineNotice(),
        ],
      ],
    );
  }
}

class _RefusedBody extends StatelessWidget {
  const _RefusedBody(this.parent);

  final StripResultScreen parent;

  /// Every refusal gets a concrete, physical instruction. "Correction residual
  /// too high" is true but useless to somebody standing in a market.
  String _advice(String? reason) {
    return switch (reason) {
      RejectReason.referenceCardNotFound =>
        'Lay the SATVA card flat next to the strip and make sure the whole card is '
            'inside the frame.',
      RejectReason.referenceCardIncomplete =>
        'Part of the card is hidden. Move anything covering it and try again.',
      RejectReason.exposureOutOfRange =>
        'Move somewhere with even, moderate light — not direct sun, not deep shade.',
      RejectReason.clippedHighlights =>
        'There is glare on the card. Turn away from the light source or shade it '
            'with your hand.',
      RejectReason.clippedShadows => 'It is too dark here. Find brighter light.',
      RejectReason.illuminantTooTinted =>
        'The light here is strongly coloured, which SATVA cannot correct for. '
            'Step into daylight if you can.',
      RejectReason.nonUniformIllumination =>
        'A shadow is falling across the card. Move your hand or body out of the light.',
      RejectReason.correctionResidualTooHigh =>
        'The colours on the card did not read correctly. Hold the phone square to the '
            'card, about 20 cm away, and retake the photo.',
      RejectReason.stripRegionNotFound =>
        'Place the strip inside the marked window on the card.',
      RejectReason.stripRegionNotUniform =>
        'The strip looks uneven. Make sure it is flat, fully wetted, and not creased.',
      RejectReason.outOfCalibrationRange =>
        'The colour does not match this test. Check you selected the right test, and '
            'that the strip has finished reacting.',
      RejectReason.unknownReagent => 'That test is not available in this version.',
      _ => 'Retake the photo with the card flat and evenly lit.',
    };
  }

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;

    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
      children: <Widget>[
        Container(
          padding: const EdgeInsets.all(22),
          decoration: BoxDecoration(
            color: SatvaColors.refusedSoft,
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: SatvaColors.line),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  const Icon(Icons.do_not_disturb_on_outlined,
                      color: SatvaColors.refused, size: 26,),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text('SATVA did not take a reading', style: text.titleLarge),
                  ),
                ],
              ),
              const SizedBox(height: 14),
              Text(
                parent.result.rejectDetail ?? 'The photo could not be measured reliably.',
                style: text.bodyLarge,
              ),
              const SizedBox(height: 16),
              Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: SatvaColors.line),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    const Icon(Icons.lightbulb_outline, size: 20, color: SatvaColors.brand),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(_advice(parent.result.rejectReason), style: text.bodyMedium),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 18),

        // This framing matters. Users are conditioned to read a refusal as the
        // app failing. Here the refusal *is* the correct behaviour, and saying
        // so keeps people from retrying until they force out a number.
        Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: SatvaColors.brandSoft,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: SatvaColors.brand.withValues(alpha: 0.22)),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              const Icon(Icons.verified_outlined, size: 20, color: SatvaColors.brand),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      'This is SATVA working correctly',
                      style: text.titleMedium?.copyWith(color: SatvaColors.brandDark),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'When the photograph is not good enough to measure accurately, SATVA '
                      'refuses rather than reporting an unreliable number. A wrong reading '
                      'attached to a complaint could harm an honest trader.',
                      style: text.bodySmall?.copyWith(color: SatvaColors.brandDark),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 24),

        FilledButton.icon(
          onPressed: parent.onRetake,
          icon: const Icon(Icons.camera_alt_outlined),
          label: const Text('Retake the photo'),
        ),
        const SizedBox(height: 10),
        OutlinedButton(
          onPressed: parent.onSave,
          child: const Text('Save this attempt and stop'),
        ),
      ],
    );
  }
}

class _ProvisionalCalibrationCard extends StatelessWidget {
  const _ProvisionalCalibrationCard();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: SatvaColors.cautionSoft,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: SatvaColors.caution.withValues(alpha: 0.28)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Icon(Icons.info_outline, size: 20, color: SatvaColors.caution),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              'This calibration has not yet been checked against an accredited '
              'laboratory. The reading is reproducible and was taken under verified '
              'conditions, but treat it as grounds for an official sample rather than '
              'as a laboratory result.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        ],
      ),
    );
  }
}

class _MeasurementQualityCard extends StatelessWidget {
  const _MeasurementQualityCard({required this.result});

  final ColorimetryResult result;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text('How this was measured', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 4),
            Text(
              'SATVA checked every one of these before reporting a number.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 12),
            const _CheckRow('Reference card found and squared up'),
            const _CheckRow('Exposure inside the usable range'),
            const _CheckRow('Light source neutral enough to correct'),
            const _CheckRow('Strip evenly coloured'),
            _CheckRow(
              'Colour correction accurate to '
              'dE ${(result.correctionResidualDe ?? 0).toStringAsFixed(2)}',
            ),
          ],
        ),
      ),
    );
  }
}

class _CheckRow extends StatelessWidget {
  const _CheckRow(this.label);

  final String label;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Icon(Icons.check_circle, size: 17, color: SatvaColors.clear),
          const SizedBox(width: 10),
          Expanded(child: Text(label, style: Theme.of(context).textTheme.bodyMedium)),
        ],
      ),
    );
  }
}

class _OfflineNotice extends StatelessWidget {
  const _OfflineNotice();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: SatvaColors.unsureSoft,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: SatvaColors.unsure.withValues(alpha: 0.25)),
      ),
      child: Row(
        children: <Widget>[
          const Icon(Icons.cloud_off_outlined, size: 18, color: SatvaColors.unsure),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              'You are offline. This reading is saved on your phone and will sync '
              'automatically when you have a connection.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        ],
      ),
    );
  }
}
