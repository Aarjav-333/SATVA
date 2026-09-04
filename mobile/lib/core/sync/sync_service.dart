/// Background synchronisation of the offline queue.
///
/// Runs when connectivity returns and on a slow timer while online. Every step
/// is safe to repeat: `client_scan_uid` is the server's idempotency key, so a
/// sync interrupted halfway leaves no duplicates behind.
///
/// The order matters. A scan is created first, then its reading is attached,
/// then images are uploaded, then sharing is applied. Attaching a reading to a
/// scan that does not exist yet would fail, and sharing before the reading
/// exists would be refused by the evidence rule — correctly, but pointlessly.
library;

import 'dart:async';
import 'dart:io';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';

import '../api/api_client.dart';
import '../storage/local_database.dart';
import '../storage/scan_repository.dart';

class SyncOutcome {
  const SyncOutcome({
    required this.attempted,
    required this.synced,
    required this.failed,
    required this.rejected,
    this.offline = false,
  });

  final int attempted;
  final int synced;
  final int failed;
  final int rejected;
  final bool offline;

  static const SyncOutcome idle =
      SyncOutcome(attempted: 0, synced: 0, failed: 0, rejected: 0);

  static const SyncOutcome offlineResult =
      SyncOutcome(attempted: 0, synced: 0, failed: 0, rejected: 0, offline: true);
}

class SyncService {
  SyncService({
    required ApiClient api,
    required ScanRepository repository,
    Connectivity? connectivity,
  })  : _api = api,
        _repository = repository,
        _connectivity = connectivity ?? Connectivity();

  final ApiClient _api;
  final ScanRepository _repository;
  final Connectivity _connectivity;

  StreamSubscription<List<ConnectivityResult>>? _subscription;
  Timer? _timer;
  bool _running = false;

  final StreamController<SyncOutcome> _outcomes =
      StreamController<SyncOutcome>.broadcast();

  Stream<SyncOutcome> get outcomes => _outcomes.stream;

  /// Start watching for connectivity and syncing periodically.
  void start({Duration interval = const Duration(minutes: 5)}) {
    _subscription = _connectivity.onConnectivityChanged.listen(
      (List<ConnectivityResult> results) {
        final bool online =
            results.any((ConnectivityResult r) => r != ConnectivityResult.none);
        if (online) {
          // Connectivity events fire before the interface is usable; a short
          // delay avoids a guaranteed-failed first attempt that would burn a
          // backoff step.
          Timer(const Duration(seconds: 2), () => unawaited(syncNow()));
        }
      },
    );
    _timer = Timer.periodic(interval, (_) => unawaited(syncNow()));
  }

  void dispose() {
    _subscription?.cancel();
    _timer?.cancel();
    _outcomes.close();
  }

  Future<bool> isOnline() async {
    final List<ConnectivityResult> results = await _connectivity.checkConnectivity();
    return results.any((ConnectivityResult r) => r != ConnectivityResult.none);
  }

  /// Drain the queue. Safe to call at any time; concurrent calls are ignored.
  Future<SyncOutcome> syncNow() async {
    if (_running) return SyncOutcome.idle;
    if (!_api.isAuthenticated) return SyncOutcome.idle;
    if (!await isOnline()) {
      _outcomes.add(SyncOutcome.offlineResult);
      return SyncOutcome.offlineResult;
    }

    _running = true;
    int synced = 0, failed = 0, rejected = 0;

    try {
      final List<Map<String, Object?>> pending = await _repository.pendingForSync();
      for (final Map<String, Object?> row in pending) {
        final String uid = row['client_scan_uid']! as String;
        try {
          await _syncOne(row);
          synced++;
        } on ApiException catch (e) {
          if (e.isPermanent) {
            await _repository.markRejected(uid, '${e.code}: ${e.message}');
            rejected++;
          } else {
            await _repository.markFailed(uid, '${e.code}: ${e.message}');
            failed++;
          }
        } on OfflineException {
          // Connectivity dropped mid-drain. Stop cleanly; the rest stays queued.
          break;
        } catch (e) {
          await _repository.markFailed(uid, e.toString());
          failed++;
        }
      }
    } finally {
      _running = false;
    }

    final SyncOutcome outcome = SyncOutcome(
      attempted: synced + failed + rejected,
      synced: synced,
      failed: failed,
      rejected: rejected,
    );
    _outcomes.add(outcome);
    return outcome;
  }

  Future<void> _syncOne(Map<String, Object?> row) async {
    final String uid = row['client_scan_uid']! as String;

    // 1. Create (or recover) the server-side scan. The endpoint is idempotent
    // on client_scan_uid, so a retry after a response we never saw returns the
    // original record rather than creating a second one.
    String? serverId = row['server_scan_id'] as String?;
    if (serverId == null) {
      final Map<String, dynamic> payload = await _repository.toSyncPayload(row);
      final Map<String, dynamic> created = await _api.createScan(payload);
      serverId = created['id'] as String;
    }

    // 2. Attach the colorimetric reading, if one is pending. This is what
    // promotes the scan to evidence grade on the server.
    final Map<String, dynamic>? reading =
        await _repository.pendingReadingPayload(uid);
    if (reading != null) {
      await _api.attachReading(serverId, reading);
    }

    // 3. Upload images. Failures here are logged but do not fail the scan: the
    // reading is the evidence, and a photograph that failed to upload can be
    // retried without losing the number.
    await _uploadImages(uid, serverId);

    // 4. Apply the user's sharing choice, now that a reading exists to justify
    // it. Doing this earlier would hit the evidence rule.
    if ((row['shared_with_watch'] as int? ?? 0) == 1) {
      try {
        await _api.shareScan(serverId, shared: true);
      } on ApiException catch (e) {
        if (!e.isEvidenceRuleViolation) rethrow;
        debugPrint('SATVA: sharing refused by the evidence rule for $uid: ${e.message}');
      }
    }

    await _repository.markSynced(uid, serverId);
  }

  Future<void> _uploadImages(String uid, String serverScanId) async {
    final List<PendingImage> images = await _repository.pendingImages(uid);

    for (final PendingImage image in images) {
      final File file = File(image.localPath);
      if (!file.existsSync()) {
        // The OS reclaimed the cache directory. Nothing to retry.
        await _repository.markImageStatus(image.id, SyncStatus.rejected);
        continue;
      }
      try {
        await _api.uploadImage(
          '/scans/$serverScanId/images',
          await file.readAsBytes(),
          filename: image.localPath.split(Platform.pathSeparator).last,
          kind: image.kind,
        );
        await _repository.markImageStatus(image.id, SyncStatus.synced);
      } catch (e) {
        debugPrint('SATVA: image upload failed for $uid: $e');
      }
    }
  }
}
