/// Riverpod providers wiring the app together.
///
/// Everything the Scan flow needs is available offline. The API client and sync
/// service exist, but no screen awaits them: a scan completes entirely on the
/// handset and syncs later.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../features/colorimetry/engine/calibration.dart';
import '../features/scan/vision/tflite_model.dart';
import '../features/scan/vision/vision_service.dart';
import 'api/api_client.dart';
import 'storage/local_database.dart';
import 'storage/scan_repository.dart';
import 'sync/sync_service.dart';

/// API base URL. Overridden at build time with
/// `--dart-define=SATVA_API_BASE=http://10.0.2.2:8000/api/v1`
/// (10.0.2.2 is the host machine as seen from the Android emulator).
const String kApiBase = String.fromEnvironment(
  'SATVA_API_BASE',
  defaultValue: 'http://10.0.2.2:8000/api/v1',
);

final Provider<ApiClient> apiClientProvider = Provider<ApiClient>((Ref ref) {
  final ApiClient client = ApiClient(baseUrl: kApiBase);
  ref.onDispose(client.close);
  return client;
});

final FutureProvider<LocalDatabase> databaseProvider =
    FutureProvider<LocalDatabase>((Ref ref) => LocalDatabase.open());

final FutureProvider<ScanRepository> scanRepositoryProvider =
    FutureProvider<ScanRepository>((Ref ref) async {
  final LocalDatabase db = await ref.watch(databaseProvider.future);
  return ScanRepository(db);
});

final FutureProvider<SyncService> syncServiceProvider =
    FutureProvider<SyncService>((Ref ref) async {
  final ScanRepository repository = await ref.watch(scanRepositoryProvider.future);
  final SyncService service = SyncService(
    api: ref.watch(apiClientProvider),
    repository: repository,
  )..start();
  ref.onDispose(service.dispose);
  return service;
});

/// The screening model. Loaded once and reused: constructing a TFLite
/// interpreter per scan would add hundreds of milliseconds to a flow the
/// specification budgets at ten seconds end to end.
final FutureProvider<VisionScreener> visionScreenerProvider =
    FutureProvider<VisionScreener>((Ref ref) async {
  final VisionScreener screener = await createScreener();
  ref.onDispose(screener.dispose);
  return screener;
});

final FutureProvider<CalibrationRegistry> calibrationProvider =
    FutureProvider<CalibrationRegistry>((Ref ref) => CalibrationRegistry.load());

final FutureProvider<SharedPreferences> prefsProvider =
    FutureProvider<SharedPreferences>((Ref ref) => SharedPreferences.getInstance());

/// Count of scans waiting to sync, for the offline indicator.
final FutureProvider<int> pendingSyncCountProvider = FutureProvider<int>((Ref ref) async {
  final ScanRepository repository = await ref.watch(scanRepositoryProvider.future);
  return repository.pendingCount();
});

/// Local scan history.
final FutureProvider<List<LocalScan>> scanHistoryProvider =
    FutureProvider<List<LocalScan>>((Ref ref) async {
  final ScanRepository repository = await ref.watch(scanRepositoryProvider.future);
  return repository.history();
});

/// Crops the app will screen.
///
/// Mirrors `app.core.constants.SUPPORTED_CROPS`. Bundled rather than fetched so
/// a first-run handset with no connectivity still knows what it can and cannot
/// scan — and, importantly, can explain a refusal (specification rule 15).
class CropCatalogue {
  const CropCatalogue._();

  static const Map<String, String> supported = <String, String>{
    'mango': 'Mango',
    'banana': 'Banana',
    'papaya': 'Papaya',
    'tomato': 'Tomato',
  };

  static const Map<String, String> refused = <String, String>{
    'sapota': 'Sapota',
    'guava': 'Guava',
    'custard_apple': 'Custard apple',
    'pineapple': 'Pineapple',
    'grape': 'Grapes',
  };

  static const String refusalNote =
      'SATVA has not been validated for this produce yet, so it will not score it. '
      'Scoring an unvalidated crop would be a guess presented as a measurement.';

  static bool isSupported(String crop) => supported.containsKey(crop);
}

/// Standing disclaimer text. Mirrors `app.core.constants.DISCLAIMER_LONG` so the
/// wording cannot drift between the app, the dashboards and the complaint PDF.
class Disclaimers {
  const Disclaimers._();

  static const String short =
      'SATVA is a screening aid, not a statutory test or certification.';

  static const String long =
      'SATVA is a screening aid. It is not a statutory test, a certification, or a '
      'legal determination of food safety. A visual screening result is advisory '
      'only and must never be treated as proof of adulteration. Only a confirmatory '
      'colorimetric strip reading may be escalated, and escalation is routed to the '
      'statutory FSSAI channel rather than replacing it.';

  static const String photoNeverConvicts =
      'A photograph alone is never treated as evidence. This score only decides '
      'whether a ₹5 strip test is worth doing.';
}
