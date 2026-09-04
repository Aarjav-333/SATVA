/// SATVA — Scan · Analyse · Trace · Verify · Alert
///
/// A food-safety screening instrument for the person holding the fruit.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/providers.dart';
import 'core/theme.dart';
import 'features/home/home_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ProviderScope(child: SatvaApp()));
}

class SatvaApp extends ConsumerWidget {
  const SatvaApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return MaterialApp(
      title: 'SATVA',
      debugShowCheckedModeBanner: false,
      theme: SatvaTheme.light(),
      home: const _Bootstrap(),
    );
  }
}

/// Warms the two things a scan cannot start without: the calibration tables and
/// the screening model.
///
/// Both are loaded from bundled assets, so this works on a first run with no
/// connectivity — which is the whole point of an offline-first design. If either
/// fails the app still opens; the Scan flow reports the specific problem rather
/// than the app dying at launch.
class _Bootstrap extends ConsumerWidget {
  const _Bootstrap();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final AsyncValue<void> calibration = ref.watch(calibrationProvider);
    final AsyncValue<void> screener = ref.watch(visionScreenerProvider);

    if (calibration.isLoading || screener.isLoading) {
      return const _SplashScreen();
    }
    return const HomeScreen();
  }
}

class _SplashScreen extends StatelessWidget {
  const _SplashScreen();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: SatvaColors.brand,
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: <Widget>[
            const Text(
              'SATVA',
              style: TextStyle(
                fontSize: 48,
                fontWeight: FontWeight.w800,
                color: Colors.white,
                letterSpacing: 6,
              ),
            ),
            const SizedBox(height: 10),
            Text(
              'Scan · Analyse · Trace · Verify · Alert',
              style: TextStyle(
                fontSize: 13.5,
                color: Colors.white.withValues(alpha: 0.85),
                letterSpacing: 0.4,
              ),
            ),
            const SizedBox(height: 44),
            SizedBox(
              width: 26,
              height: 26,
              child: CircularProgressIndicator(
                strokeWidth: 2.4,
                color: Colors.white.withValues(alpha: 0.9),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
