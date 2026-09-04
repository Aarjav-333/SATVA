/// SATVA visual language.
///
/// Designed for the actual conditions of use: a bright market, one hand on the
/// phone and one on a mango, ten seconds to a decision.
///
/// * High contrast throughout — a pastel palette is unreadable in sunlight.
/// * Large touch targets and large result type.
/// * Result colours are **advisory**, never verdict colours. The suspicious
///   state is amber, not red: red says "guilty", and SATVA never says that from
///   a photograph.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

class SatvaColors {
  const SatvaColors._();

  static const Color ink = Color(0xFF101828);
  static const Color inkSoft = Color(0xFF475467);
  static const Color inkFaint = Color(0xFF98A2B3);
  static const Color line = Color(0xFFE4E7EC);
  static const Color surface = Color(0xFFFFFFFF);
  static const Color canvas = Color(0xFFF7F8FA);

  /// SATVA green: harvest, safety, the natural alternative.
  static const Color brand = Color(0xFF0B6E4F);
  static const Color brandDark = Color(0xFF075139);
  static const Color brandSoft = Color(0xFFE7F4EF);

  /// Clear result.
  static const Color clear = Color(0xFF067647);
  static const Color clearSoft = Color(0xFFECFDF3);

  /// Inconclusive — the model is unsure and says so.
  static const Color unsure = Color(0xFF5B7FBE);
  static const Color unsureSoft = Color(0xFFEFF4FF);

  /// Suspicious — advisory amber, deliberately not red.
  static const Color caution = Color(0xFFB54708);
  static const Color cautionSoft = Color(0xFFFFF6ED);

  /// Confirmed chemical exceedance. The only place a strong warning colour is
  /// used, and only ever after a strip reading.
  static const Color confirmed = Color(0xFFB42318);
  static const Color confirmedSoft = Color(0xFFFEF3F2);

  /// Refusal — SATVA declining to measure. Neutral slate, because a refusal is
  /// not a failure and must not read as one.
  static const Color refused = Color(0xFF475467);
  static const Color refusedSoft = Color(0xFFF2F4F7);
}

class SatvaTheme {
  const SatvaTheme._();

  static ThemeData light() {
    final ColorScheme scheme = ColorScheme.fromSeed(
      seedColor: SatvaColors.brand,
      primary: SatvaColors.brand,
      surface: SatvaColors.surface,
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: SatvaColors.canvas,
      // System font: it ships with Indic script support, which a bundled Latin
      // font would not, and Malayalam/Tamil/Hindi are on the roadmap.
      fontFamily: null,
      appBarTheme: const AppBarTheme(
        backgroundColor: SatvaColors.surface,
        foregroundColor: SatvaColors.ink,
        elevation: 0,
        scrolledUnderElevation: 1,
        centerTitle: false,
        systemOverlayStyle: SystemUiOverlayStyle.dark,
        titleTextStyle: TextStyle(
          fontSize: 18,
          fontWeight: FontWeight.w700,
          color: SatvaColors.ink,
          letterSpacing: -0.2,
        ),
      ),
      textTheme: const TextTheme(
        displaySmall: TextStyle(
          fontSize: 44,
          fontWeight: FontWeight.w800,
          color: SatvaColors.ink,
          letterSpacing: -1.2,
          height: 1.05,
        ),
        headlineMedium: TextStyle(
          fontSize: 26,
          fontWeight: FontWeight.w700,
          color: SatvaColors.ink,
          letterSpacing: -0.5,
        ),
        titleLarge: TextStyle(
          fontSize: 19,
          fontWeight: FontWeight.w700,
          color: SatvaColors.ink,
          letterSpacing: -0.2,
        ),
        titleMedium: TextStyle(
          fontSize: 16,
          fontWeight: FontWeight.w600,
          color: SatvaColors.ink,
        ),
        bodyLarge: TextStyle(fontSize: 16, height: 1.45, color: SatvaColors.ink),
        bodyMedium: TextStyle(fontSize: 14.5, height: 1.45, color: SatvaColors.inkSoft),
        bodySmall: TextStyle(fontSize: 12.5, height: 1.4, color: SatvaColors.inkSoft),
        labelLarge: TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
      ),
      cardTheme: CardThemeData(
        color: SatvaColors.surface,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: SatvaColors.line),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: SatvaColors.brand,
          foregroundColor: Colors.white,
          // 56pt: usable with a thumb while holding fruit in the other hand.
          minimumSize: const Size.fromHeight(56),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          textStyle: const TextStyle(fontSize: 16.5, fontWeight: FontWeight.w700),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: SatvaColors.ink,
          minimumSize: const Size.fromHeight(52),
          side: const BorderSide(color: SatvaColors.line, width: 1.5),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(foregroundColor: SatvaColors.brand),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: SatvaColors.canvas,
        side: const BorderSide(color: SatvaColors.line),
        labelStyle: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
      dividerTheme: const DividerThemeData(color: SatvaColors.line, thickness: 1, space: 1),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: SatvaColors.ink,
        contentTextStyle: const TextStyle(color: Colors.white, fontSize: 14.5),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
    );
  }
}

/// Palette for one result state.
class ResultPalette {
  const ResultPalette(this.foreground, this.background, this.icon, this.label);

  final Color foreground;
  final Color background;
  final IconData icon;
  final String label;

  static const ResultPalette clear = ResultPalette(
    SatvaColors.clear,
    SatvaColors.clearSoft,
    Icons.check_circle_outline,
    'Nothing unusual seen',
  );

  static const ResultPalette unsure = ResultPalette(
    SatvaColors.unsure,
    SatvaColors.unsureSoft,
    Icons.help_outline,
    'Not sure',
  );

  static const ResultPalette caution = ResultPalette(
    SatvaColors.caution,
    SatvaColors.cautionSoft,
    Icons.science_outlined,
    'Worth testing',
  );

  static const ResultPalette confirmed = ResultPalette(
    SatvaColors.confirmed,
    SatvaColors.confirmedSoft,
    Icons.report_gmailerrorred_outlined,
    'Confirmed by strip test',
  );

  static const ResultPalette refused = ResultPalette(
    SatvaColors.refused,
    SatvaColors.refusedSoft,
    Icons.do_not_disturb_on_outlined,
    'Could not measure',
  );
}
