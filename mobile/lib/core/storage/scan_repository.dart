/// Local persistence for scans, readings and images.
///
/// Everything the Scan flow writes goes through here first. Nothing waits on the
/// network: a scan is durable on the handset the moment it is taken, and sync is
/// a separate, retryable concern.
library;

import 'dart:convert';

import 'package:sqflite/sqflite.dart';
import 'package:uuid/uuid.dart';

import '../../features/colorimetry/engine/pipeline.dart';
import '../../features/scan/vision/vision_service.dart';
import 'local_database.dart';

const Uuid _uuid = Uuid();

/// Evidence grades, mirroring `app.core.constants.EvidenceGrade`.
class EvidenceGrade {
  static const String screeningOnly = 'screening_only';
  static const String confirmatory = 'confirmatory';
  static const String rejected = 'rejected';
}

class LocalScan {
  const LocalScan({
    required this.clientScanUid,
    required this.crop,
    required this.capturedAt,
    required this.evidenceGrade,
    required this.syncStatus,
    this.serverScanId,
    this.cultivar,
    this.anomalyScore,
    this.visionVerdict,
    this.visionModelId,
    this.visionModelKind,
    this.ripenessIndex,
    this.latitude,
    this.longitude,
    this.locationAccuracyM,
    this.merchantRef,
    this.lotId,
    this.sharedWithWatch = false,
    this.syncAttempts = 0,
    this.lastError,
    this.reading,
  });

  final String clientScanUid;
  final String? serverScanId;
  final String crop;
  final String? cultivar;
  final DateTime capturedAt;
  final String evidenceGrade;
  final String syncStatus;
  final double? anomalyScore;
  final String? visionVerdict;
  final String? visionModelId;
  final String? visionModelKind;
  final double? ripenessIndex;
  final double? latitude;
  final double? longitude;
  final double? locationAccuracyM;
  final String? merchantRef;
  final String? lotId;
  final bool sharedWithWatch;
  final int syncAttempts;
  final String? lastError;
  final LocalReading? reading;

  bool get isConfirmed => evidenceGrade == EvidenceGrade.confirmatory;

  /// The gate the UI consults. Mirrors `app.services.evidence`: a photograph
  /// alone can never be escalated.
  bool get canEscalate => isConfirmed;

  factory LocalScan.fromRow(Map<String, Object?> row, {LocalReading? reading}) {
    return LocalScan(
      clientScanUid: row['client_scan_uid']! as String,
      serverScanId: row['server_scan_id'] as String?,
      crop: row['crop']! as String,
      cultivar: row['cultivar'] as String?,
      capturedAt: DateTime.parse(row['captured_at']! as String),
      evidenceGrade: row['evidence_grade']! as String,
      syncStatus: row['sync_status']! as String,
      anomalyScore: (row['anomaly_score'] as num?)?.toDouble(),
      visionVerdict: row['vision_verdict'] as String?,
      visionModelId: row['vision_model_id'] as String?,
      visionModelKind: row['vision_model_kind'] as String?,
      ripenessIndex: (row['ripeness_index'] as num?)?.toDouble(),
      latitude: (row['latitude'] as num?)?.toDouble(),
      longitude: (row['longitude'] as num?)?.toDouble(),
      locationAccuracyM: (row['location_accuracy_m'] as num?)?.toDouble(),
      merchantRef: row['merchant_ref'] as String?,
      lotId: row['lot_id'] as String?,
      sharedWithWatch: (row['shared_with_watch'] as int? ?? 0) == 1,
      syncAttempts: row['sync_attempts'] as int? ?? 0,
      lastError: row['last_error'] as String?,
      reading: reading,
    );
  }
}

class LocalReading {
  const LocalReading({
    required this.id,
    required this.assay,
    required this.accepted,
    this.concentrationValue,
    this.concentrationUnit,
    this.ciLow,
    this.ciHigh,
    this.bandLabel,
    this.exceedsActionThreshold = false,
    this.isLabValidated = false,
    this.rejectReason,
    this.rejectDetail,
  });

  final String id;
  final String assay;
  final bool accepted;
  final double? concentrationValue;
  final String? concentrationUnit;
  final double? ciLow;
  final double? ciHigh;
  final String? bandLabel;
  final bool exceedsActionThreshold;
  final bool isLabValidated;
  final String? rejectReason;
  final String? rejectDetail;

  factory LocalReading.fromRow(Map<String, Object?> row) {
    return LocalReading(
      id: row['id']! as String,
      assay: row['assay']! as String,
      accepted: (row['accepted'] as int) == 1,
      concentrationValue: (row['concentration_value'] as num?)?.toDouble(),
      concentrationUnit: row['concentration_unit'] as String?,
      ciLow: (row['ci_low'] as num?)?.toDouble(),
      ciHigh: (row['ci_high'] as num?)?.toDouble(),
      bandLabel: row['band_label'] as String?,
      exceedsActionThreshold: (row['exceeds_action_threshold'] as int? ?? 0) == 1,
      isLabValidated: (row['is_lab_validated'] as int? ?? 0) == 1,
      rejectReason: row['reject_reason'] as String?,
      rejectDetail: row['reject_detail'] as String?,
    );
  }
}

/// An image queued for upload.
class PendingImage {
  const PendingImage({
    required this.id,
    required this.kind,
    required this.localPath,
    required this.sha256,
    required this.byteSize,
  });

  final String id;
  final String kind;
  final String localPath;
  final String sha256;
  final int byteSize;
}

class ScanRepository {
  ScanRepository(this._database);

  final LocalDatabase _database;

  Database get _db => _database.raw;

  String newScanUid() => 'satva-${_uuid.v4().replaceAll('-', '').substring(0, 24)}';

  /// Persist a Layer-A screening result. Always `screening_only`: only an
  /// accepted colorimetric reading can change that.
  Future<String> saveScreening({
    required String crop,
    required VisionResult vision,
    String? cultivar,
    double? latitude,
    double? longitude,
    double? locationAccuracyM,
    String? merchantRef,
    String? lotId,
    String? perceptualHash,
    bool capturedOffline = true,
  }) async {
    final String uid = newScanUid();
    final String now = DateTime.now().toUtc().toIso8601String();

    await _db.insert('scans', <String, Object?>{
      'client_scan_uid': uid,
      'crop': crop,
      'cultivar': cultivar,
      'captured_at': now,
      'captured_offline': capturedOffline ? 1 : 0,
      'anomaly_score': vision.anomalyScore,
      'vision_verdict': vision.verdict.wire,
      'vision_model_id': vision.modelId,
      'vision_model_kind': vision.modelKind,
      'vision_inference_ms': vision.inferenceMs,
      'ripeness_index': vision.ripenessIndex,
      'saliency_json':
          jsonEncode(vision.saliency.map((SaliencyRegion r) => r.toJson()).toList()),
      'evidence_grade': EvidenceGrade.screeningOnly,
      'latitude': latitude,
      'longitude': longitude,
      'location_accuracy_m': locationAccuracyM,
      'merchant_ref': merchantRef,
      'lot_id': lotId,
      'shared_with_watch': 0,
      'perceptual_hash': perceptualHash,
      'sync_status': SyncStatus.pending,
      'created_at': now,
      'updated_at': now,
    });
    return uid;
  }

  /// Attach a colorimetric result and re-grade the scan.
  ///
  /// This is the local mirror of `evidence.promote_to_confirmatory`. An accepted
  /// reading promotes to `confirmatory`; a refusal grades `rejected` and is
  /// still stored, because a refusal is a meaningful record — it shows SATVA
  /// declined to measure rather than guessing.
  Future<void> attachReading(String clientScanUid, ColorimetryResult result) async {
    final String now = DateTime.now().toUtc().toIso8601String();

    await _db.transaction((Transaction txn) async {
      await txn.insert('readings', <String, Object?>{
        'id': _uuid.v4(),
        'client_scan_uid': clientScanUid,
        'assay': result.assay,
        'calibration_id': result.calibrationId ?? '',
        'pipeline_version': pipelineVersion,
        'accepted': result.accepted ? 1 : 0,
        'reject_reason': result.rejectReason,
        'reject_detail': result.rejectDetail,
        'concentration_value': result.concentration,
        'concentration_unit': result.unit,
        'ci_low': result.ciLow,
        'ci_high': result.ciHigh,
        'band_label': result.bandLabel,
        'exceeds_action_threshold': result.exceedsActionThreshold ? 1 : 0,
        'is_lab_validated': result.isLabValidated ? 1 : 0,
        'delta_e_nearest': result.deltaENearestStop,
        'strip_lab_json': jsonEncode(result.stripLabCorrected?.toList()),
        'correction_residual_de': result.correctionResidualDe,
        'quality_json': jsonEncode(result.quality),
        'sync_status': SyncStatus.pending,
        'created_at': now,
      });

      await txn.update(
        'scans',
        <String, Object?>{
          'evidence_grade':
              result.accepted ? EvidenceGrade.confirmatory : EvidenceGrade.rejected,
          'sync_status': SyncStatus.pending,
          'updated_at': now,
        },
        where: 'client_scan_uid = ?',
        whereArgs: <Object?>[clientScanUid],
      );
    });
  }

  Future<void> setSharedWithWatch(String clientScanUid, bool shared) async {
    // Enforced locally as well as on the server: a screening-only scan must not
    // even be able to leave the handset marked for sharing.
    final LocalScan? scan = await findByUid(clientScanUid);
    if (scan == null) return;
    if (shared && !scan.canEscalate) {
      throw StateError(
        'This scan has only been screened visually. Complete the confirmatory '
        'strip test before sharing it with SATVA Watch.',
      );
    }
    await _db.update(
      'scans',
      <String, Object?>{
        'shared_with_watch': shared ? 1 : 0,
        'sync_status': SyncStatus.pending,
        'updated_at': DateTime.now().toUtc().toIso8601String(),
      },
      where: 'client_scan_uid = ?',
      whereArgs: <Object?>[clientScanUid],
    );
  }

  Future<void> recordImage({
    required String clientScanUid,
    required String kind,
    required String localPath,
    required String sha256,
    required int byteSize,
  }) async {
    await _db.insert('images', <String, Object?>{
      'id': _uuid.v4(),
      'client_scan_uid': clientScanUid,
      'kind': kind,
      'local_path': localPath,
      'sha256': sha256,
      'byte_size': byteSize,
      'sync_status': SyncStatus.pending,
      'created_at': DateTime.now().toUtc().toIso8601String(),
    });
  }

  Future<LocalScan?> findByUid(String clientScanUid) async {
    final List<Map<String, Object?>> rows = await _db.query(
      'scans',
      where: 'client_scan_uid = ?',
      whereArgs: <Object?>[clientScanUid],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return LocalScan.fromRow(rows.first, reading: await _latestReading(clientScanUid));
  }

  Future<LocalReading?> _latestReading(String clientScanUid) async {
    final List<Map<String, Object?>> rows = await _db.query(
      'readings',
      where: 'client_scan_uid = ?',
      whereArgs: <Object?>[clientScanUid],
      orderBy: 'created_at DESC',
      limit: 1,
    );
    return rows.isEmpty ? null : LocalReading.fromRow(rows.first);
  }

  Future<List<LocalScan>> history({int limit = 100}) async {
    final List<Map<String, Object?>> rows = await _db.query(
      'scans',
      orderBy: 'captured_at DESC',
      limit: limit,
    );
    return <LocalScan>[
      for (final Map<String, Object?> row in rows)
        LocalScan.fromRow(
          row,
          reading: await _latestReading(row['client_scan_uid']! as String),
        ),
    ];
  }

  /// Scans due for a sync attempt.
  Future<List<Map<String, Object?>>> pendingForSync({int limit = 50}) async {
    final String now = DateTime.now().toUtc().toIso8601String();
    return _db.query(
      'scans',
      where: 'sync_status IN (?, ?) AND (next_attempt_at IS NULL OR next_attempt_at <= ?)',
      whereArgs: <Object?>[SyncStatus.pending, SyncStatus.syncing, now],
      orderBy: 'captured_at ASC',
      limit: limit,
    );
  }

  Future<int> pendingCount() async {
    final List<Map<String, Object?>> rows = await _db.rawQuery(
      "SELECT COUNT(*) AS n FROM scans WHERE sync_status = 'pending'",
    );
    return (rows.first['n'] as int?) ?? 0;
  }

  Future<void> markSynced(String clientScanUid, String serverScanId) async {
    await _db.update(
      'scans',
      <String, Object?>{
        'sync_status': SyncStatus.synced,
        'server_scan_id': serverScanId,
        'last_error': null,
        'updated_at': DateTime.now().toUtc().toIso8601String(),
      },
      where: 'client_scan_uid = ?',
      whereArgs: <Object?>[clientScanUid],
    );
    await _db.update(
      'readings',
      <String, Object?>{'sync_status': SyncStatus.synced},
      where: 'client_scan_uid = ?',
      whereArgs: <Object?>[clientScanUid],
    );
  }

  /// Record a transient failure and schedule the next attempt.
  ///
  /// Exponential backoff capped at an hour: a handset that has been offline for
  /// a week must not hammer the API the moment it reconnects, and a server
  /// having a bad day must not be made worse by every device retrying in
  /// lockstep.
  Future<void> markFailed(String clientScanUid, String error) async {
    final List<Map<String, Object?>> rows = await _db.query(
      'scans',
      columns: <String>['sync_attempts'],
      where: 'client_scan_uid = ?',
      whereArgs: <Object?>[clientScanUid],
      limit: 1,
    );
    final int attempts = ((rows.isEmpty ? 0 : rows.first['sync_attempts'] as int?) ?? 0) + 1;
    final int delaySeconds = _backoffSeconds(attempts);

    await _db.update(
      'scans',
      <String, Object?>{
        'sync_status': SyncStatus.pending,
        'sync_attempts': attempts,
        'next_attempt_at': DateTime.now()
            .toUtc()
            .add(Duration(seconds: delaySeconds))
            .toIso8601String(),
        'last_error': error.length > 300 ? error.substring(0, 300) : error,
        'updated_at': DateTime.now().toUtc().toIso8601String(),
      },
      where: 'client_scan_uid = ?',
      whereArgs: <Object?>[clientScanUid],
    );
  }

  /// Permanent failure: the server rejected the record for a reason retrying
  /// cannot fix. Kept and surfaced rather than silently discarded.
  Future<void> markRejected(String clientScanUid, String error) async {
    await _db.update(
      'scans',
      <String, Object?>{
        'sync_status': SyncStatus.rejected,
        'last_error': error.length > 300 ? error.substring(0, 300) : error,
        'updated_at': DateTime.now().toUtc().toIso8601String(),
      },
      where: 'client_scan_uid = ?',
      whereArgs: <Object?>[clientScanUid],
    );
  }

  static int _backoffSeconds(int attempts) {
    const int base = 30;
    const int cap = 3600;
    final int exponential = base * (1 << (attempts - 1).clamp(0, 10));
    return exponential > cap ? cap : exponential;
  }

  /// Build the JSON body for `POST /scans` or a `/scans/sync` batch entry.
  Future<Map<String, dynamic>> toSyncPayload(Map<String, Object?> row) async {
    final String uid = row['client_scan_uid']! as String;
    final List<dynamic> saliency = row['saliency_json'] == null
        ? <dynamic>[]
        : jsonDecode(row['saliency_json']! as String) as List<dynamic>;

    return <String, dynamic>{
      'client_scan_uid': uid,
      'crop': row['crop'],
      'cultivar': row['cultivar'],
      'captured_at': row['captured_at'],
      'captured_offline': (row['captured_offline'] as int? ?? 1) == 1,
      'app_version': '0.1.0',
      if (row['anomaly_score'] != null)
        'vision': <String, dynamic>{
          'anomaly_score': row['anomaly_score'],
          'verdict': row['vision_verdict'],
          'model_id': row['vision_model_id'],
          'model_kind': row['vision_model_kind'],
          'inference_ms': row['vision_inference_ms'],
          'ripeness_index': row['ripeness_index'],
          'saliency': saliency,
        },
      if (row['latitude'] != null && row['longitude'] != null)
        'location': <String, dynamic>{
          'latitude': row['latitude'],
          'longitude': row['longitude'],
          'accuracy_m': row['location_accuracy_m'],
        },
      'merchant_ref': row['merchant_ref'],
      'lot_id': row['lot_id'],
      'shared_with_watch': (row['shared_with_watch'] as int? ?? 0) == 1,
      'perceptual_hash': row['perceptual_hash'],
      'notes': row['notes'],
    };
  }

  Future<Map<String, dynamic>?> pendingReadingPayload(String clientScanUid) async {
    final List<Map<String, Object?>> rows = await _db.query(
      'readings',
      where: 'client_scan_uid = ? AND sync_status = ?',
      whereArgs: <Object?>[clientScanUid, SyncStatus.pending],
      orderBy: 'created_at DESC',
      limit: 1,
    );
    if (rows.isEmpty) return null;
    final Map<String, Object?> row = rows.first;

    return <String, dynamic>{
      'assay': row['assay'],
      'accepted': (row['accepted'] as int) == 1,
      'calibration_id': row['calibration_id'],
      'pipeline_version': row['pipeline_version'],
      'reject_reason': row['reject_reason'],
      'reject_detail': row['reject_detail'],
      'concentration_value': row['concentration_value'],
      'concentration_unit': row['concentration_unit'],
      'ci_low': row['ci_low'],
      'ci_high': row['ci_high'],
      'band_label': row['band_label'],
      'exceeds_action_threshold': (row['exceeds_action_threshold'] as int? ?? 0) == 1,
      'delta_e_nearest': row['delta_e_nearest'],
      'strip_lab': row['strip_lab_json'] == null
          ? null
          : jsonDecode(row['strip_lab_json']! as String),
      'correction_residual_de': row['correction_residual_de'],
      'quality': row['quality_json'] == null
          ? null
          : jsonDecode(row['quality_json']! as String),
    };
  }

  /// Images still awaiting upload for one scan.
  Future<List<PendingImage>> pendingImages(String clientScanUid) async {
    final List<Map<String, Object?>> rows = await _db.query(
      'images',
      where: 'client_scan_uid = ? AND sync_status = ?',
      whereArgs: <Object?>[clientScanUid, SyncStatus.pending],
      orderBy: 'created_at ASC',
    );
    return <PendingImage>[
      for (final Map<String, Object?> row in rows)
        PendingImage(
          id: row['id']! as String,
          kind: row['kind']! as String,
          localPath: row['local_path']! as String,
          sha256: row['sha256']! as String,
          byteSize: row['byte_size']! as int,
        ),
    ];
  }

  Future<void> markImageStatus(String imageId, String status) async {
    await _db.update(
      'images',
      <String, Object?>{'sync_status': status},
      where: 'id = ?',
      whereArgs: <Object?>[imageId],
    );
  }
}
