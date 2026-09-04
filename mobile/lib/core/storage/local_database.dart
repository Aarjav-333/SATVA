/// Local SQLite storage: the offline queue that makes SATVA usable in a market.
///
/// Specification §5.1 fixes this as a design constraint: the core scan must work
/// without network connectivity. Everything a scan needs is written here first
/// and synced later, so a user in a market with no signal gets exactly the same
/// screening experience as one with full bars.
///
/// Design points that matter:
///
/// * **Idempotency.** Every scan carries a `client_scan_uid` generated on the
///   handset. The server treats it as a unique key, so a retry after a failed
///   sync is a no-op rather than a duplicate reading in someone's ward.
/// * **Retry with backoff.** Sync failures are expected, not exceptional. Rows
///   carry an attempt count and a next-attempt time so a handset that has been
///   offline for a week does not hammer the API the moment it reconnects.
/// * **Nothing is deleted on sync.** A synced scan stays in local history so the
///   user can see their own past readings offline.
library;

import 'dart:convert';

import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

const int kSchemaVersion = 1;

/// Where a queued scan is in its lifecycle.
class SyncStatus {
  static const String pending = 'pending';
  static const String syncing = 'syncing';
  static const String synced = 'synced';

  /// Rejected by the server for a reason retrying will not fix (an unsupported
  /// crop, a validation error). Kept and surfaced rather than silently dropped.
  static const String rejected = 'rejected';
}

class LocalDatabase {
  LocalDatabase._(this._db);

  final Database _db;
  static LocalDatabase? _instance;

  Database get raw => _db;

  static Future<LocalDatabase> open({String? overridePath}) async {
    if (_instance != null) return _instance!;
    final String path = overridePath ??
        p.join((await getApplicationDocumentsDirectory()).path, 'satva.db');

    final Database db = await openDatabase(
      path,
      version: kSchemaVersion,
      onConfigure: (Database db) async {
        // Off by default in SQLite; without it the ON DELETE CASCADE below does
        // nothing and orphaned rows accumulate.
        await db.execute('PRAGMA foreign_keys = ON');
      },
      onCreate: _createSchema,
    );
    _instance = LocalDatabase._(db);
    return _instance!;
  }

  static Future<void> _createSchema(Database db, int version) async {
    await db.execute('''
      CREATE TABLE scans (
        client_scan_uid       TEXT PRIMARY KEY,
        server_scan_id        TEXT,
        crop                  TEXT NOT NULL,
        cultivar              TEXT,
        captured_at           TEXT NOT NULL,
        captured_offline      INTEGER NOT NULL DEFAULT 1,

        -- Layer A. Advisory only; never evidence.
        anomaly_score         REAL,
        vision_verdict        TEXT,
        vision_model_id       TEXT,
        vision_model_kind     TEXT,
        vision_inference_ms   INTEGER,
        ripeness_index        REAL,
        saliency_json         TEXT,

        -- Evidence state, mirrored from the server's vocabulary.
        evidence_grade        TEXT NOT NULL DEFAULT 'screening_only',

        latitude              REAL,
        longitude             REAL,
        location_accuracy_m   REAL,
        merchant_ref          TEXT,
        lot_id                TEXT,
        shared_with_watch     INTEGER NOT NULL DEFAULT 0,
        perceptual_hash       TEXT,
        notes                 TEXT,

        sync_status           TEXT NOT NULL DEFAULT 'pending',
        sync_attempts         INTEGER NOT NULL DEFAULT 0,
        next_attempt_at       TEXT,
        last_error            TEXT,
        created_at            TEXT NOT NULL,
        updated_at            TEXT NOT NULL
      )
    ''');

    await db.execute('''
      CREATE TABLE readings (
        id                    TEXT PRIMARY KEY,
        client_scan_uid       TEXT NOT NULL,
        assay                 TEXT NOT NULL,
        calibration_id        TEXT NOT NULL,
        pipeline_version      TEXT NOT NULL,
        accepted              INTEGER NOT NULL,
        reject_reason         TEXT,
        reject_detail         TEXT,
        concentration_value   REAL,
        concentration_unit    TEXT,
        ci_low                REAL,
        ci_high               REAL,
        band_label            TEXT,
        exceeds_action_threshold INTEGER NOT NULL DEFAULT 0,
        is_lab_validated      INTEGER NOT NULL DEFAULT 0,
        delta_e_nearest       REAL,
        strip_lab_json        TEXT,
        correction_residual_de REAL,
        quality_json          TEXT,
        sync_status           TEXT NOT NULL DEFAULT 'pending',
        created_at            TEXT NOT NULL,
        FOREIGN KEY (client_scan_uid) REFERENCES scans (client_scan_uid) ON DELETE CASCADE
      )
    ''');

    await db.execute('''
      CREATE TABLE images (
        id                    TEXT PRIMARY KEY,
        client_scan_uid       TEXT NOT NULL,
        kind                  TEXT NOT NULL,
        local_path            TEXT NOT NULL,
        sha256                TEXT NOT NULL,
        byte_size             INTEGER NOT NULL,
        sync_status           TEXT NOT NULL DEFAULT 'pending',
        created_at            TEXT NOT NULL,
        FOREIGN KEY (client_scan_uid) REFERENCES scans (client_scan_uid) ON DELETE CASCADE
      )
    ''');

    // Cached reference data so the app is fully functional offline from first
    // launch: calibration series, supported crops, and the last hotspot summary.
    await db.execute('''
      CREATE TABLE cache (
        key                   TEXT PRIMARY KEY,
        value_json            TEXT NOT NULL,
        fetched_at            TEXT NOT NULL
      )
    ''');

    await db.execute(
      'CREATE INDEX idx_scans_sync ON scans (sync_status, next_attempt_at)',
    );
    await db.execute('CREATE INDEX idx_scans_captured ON scans (captured_at DESC)');
    await db.execute('CREATE INDEX idx_readings_scan ON readings (client_scan_uid)');
    await db.execute('CREATE INDEX idx_images_scan ON images (client_scan_uid)');
  }

  // --- Cache -------------------------------------------------------------
  Future<void> putCache(String key, Object value) async {
    await _db.insert(
      'cache',
      <String, Object?>{
        'key': key,
        'value_json': jsonEncode(value),
        'fetched_at': DateTime.now().toUtc().toIso8601String(),
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  Future<Map<String, dynamic>?> getCache(String key) async {
    final List<Map<String, Object?>> rows = await _db.query(
      'cache',
      where: 'key = ?',
      whereArgs: <Object?>[key],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return jsonDecode(rows.first['value_json']! as String) as Map<String, dynamic>;
  }

  Future<void> close() async {
    await _db.close();
    _instance = null;
  }
}
