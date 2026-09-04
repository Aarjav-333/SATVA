/// HTTP client for the SATVA API.
///
/// Offline is the normal case, not an error case. Every method here can fail
/// with [OfflineException], and callers are expected to carry on — the scan is
/// already durable in SQLite, and sync will retry.
library;

import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

class ApiException implements Exception {
  ApiException(this.statusCode, this.code, this.message, [this.details]);

  final int statusCode;
  final String code;
  final String message;
  final Map<String, dynamic>? details;

  /// True when retrying cannot help: the server understood the request and
  /// refused it on its merits.
  bool get isPermanent =>
      statusCode == 400 ||
      statusCode == 403 ||
      statusCode == 409 ||
      statusCode == 413 ||
      statusCode == 422;

  /// The server refused because the scan is not evidence-grade. Surfaced
  /// distinctly so the UI can explain the two-layer rule rather than showing a
  /// generic error.
  bool get isEvidenceRuleViolation => code == 'evidence_rule_violation';

  @override
  String toString() => 'ApiException($statusCode $code): $message';
}

class OfflineException implements Exception {
  OfflineException([this.detail]);
  final String? detail;

  @override
  String toString() => 'OfflineException(${detail ?? 'no connectivity'})';
}

class ApiClient {
  ApiClient({
    required this.baseUrl,
    http.Client? httpClient,
    this.timeout = const Duration(seconds: 20),
  }) : _http = httpClient ?? http.Client();

  final String baseUrl;
  final http.Client _http;
  final Duration timeout;

  String? _accessToken;
  String? _refreshToken;
  String? _devicePseudonym;

  String? get devicePseudonym => _devicePseudonym;
  bool get isAuthenticated => _accessToken != null;

  void setTokens({String? access, String? refresh, String? device}) {
    _accessToken = access ?? _accessToken;
    _refreshToken = refresh ?? _refreshToken;
    _devicePseudonym = device ?? _devicePseudonym;
  }

  void clearTokens() {
    _accessToken = null;
    _refreshToken = null;
  }

  Map<String, String> _headers({bool json = true}) => <String, String>{
        if (json) 'Content-Type': 'application/json',
        'Accept': 'application/json',
        if (_accessToken != null) 'Authorization': 'Bearer $_accessToken',
        if (_devicePseudonym != null) 'X-SATVA-Device': _devicePseudonym!,
      };

  Uri _uri(String path, [Map<String, dynamic>? query]) {
    final Uri base = Uri.parse('$baseUrl$path');
    if (query == null || query.isEmpty) return base;
    return base.replace(
      queryParameters: query.map(
        (String k, dynamic v) => MapEntry<String, String>(k, '$v'),
      ),
    );
  }

  Future<dynamic> _send(Future<http.Response> Function() request) async {
    http.Response response;
    try {
      response = await request().timeout(timeout);
    } on SocketException catch (e) {
      throw OfflineException(e.message);
    } on HttpException catch (e) {
      throw OfflineException(e.message);
    } catch (e) {
      // A timeout is indistinguishable from being offline from the caller's
      // point of view, and both mean "queue it and try later".
      throw OfflineException(e.toString());
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      if (response.body.isEmpty) return null;
      return jsonDecode(utf8.decode(response.bodyBytes));
    }

    String code = 'http_error';
    String message = 'Request failed (${response.statusCode}).';
    Map<String, dynamic>? details;
    try {
      final Map<String, dynamic> body =
          jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
      final Map<String, dynamic>? error = body['error'] as Map<String, dynamic>?;
      if (error != null) {
        code = error['code'] as String? ?? code;
        message = error['message'] as String? ?? message;
        details = error['details'] as Map<String, dynamic>?;
      }
    } catch (_) {
      // Body was not the standard error envelope; keep the defaults.
    }
    throw ApiException(response.statusCode, code, message, details);
  }

  Future<dynamic> get(String path, [Map<String, dynamic>? query]) =>
      _send(() => _http.get(_uri(path, query), headers: _headers()));

  Future<dynamic> post(String path, [Object? body, Map<String, dynamic>? query]) =>
      _send(() => _http.post(
            _uri(path, query),
            headers: _headers(),
            body: body == null ? null : jsonEncode(body),
          ),);

  Future<dynamic> uploadImage(
    String path,
    List<int> bytes, {
    required String filename,
    required String kind,
    String contentType = 'image/jpeg',
  }) async {
    final http.MultipartRequest request =
        http.MultipartRequest('POST', _uri(path))
          ..headers.addAll(_headers(json: false))
          ..fields['kind'] = kind
          ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));

    try {
      final http.StreamedResponse streamed = await request.send().timeout(timeout);
      final http.Response response = await http.Response.fromStream(streamed);
      if (response.statusCode >= 200 && response.statusCode < 300) {
        return jsonDecode(response.body);
      }
      throw ApiException(response.statusCode, 'upload_failed', response.body);
    } on SocketException catch (e) {
      throw OfflineException(e.message);
    }
  }

  // --- Auth ---------------------------------------------------------------
  Future<Map<String, dynamic>> startOtp(String phoneNumber) async =>
      (await post('/auth/otp/start', <String, dynamic>{'phone_number': phoneNumber}))
          as Map<String, dynamic>;

  Future<Map<String, dynamic>> verifyOtp({
    required String phoneNumber,
    required String challengeId,
    required String code,
    required String deviceIdentifier,
    String? appVersion,
    String? modelName,
  }) async {
    final Map<String, dynamic> result = (await post('/auth/otp/verify', <String, dynamic>{
      'phone_number': phoneNumber,
      'challenge_id': challengeId,
      'code': code,
      'device_identifier': deviceIdentifier,
      'app_version': appVersion,
      'model_name': modelName,
    })) as Map<String, dynamic>;

    final Map<String, dynamic> tokens = result['tokens'] as Map<String, dynamic>;
    setTokens(
      access: tokens['access_token'] as String?,
      refresh: tokens['refresh_token'] as String?,
      device: result['device_pseudonym'] as String?,
    );
    return result;
  }

  // --- Reference data (cached for offline use) ---------------------------
  Future<Map<String, dynamic>> crops() async =>
      (await get('/scans/crops')) as Map<String, dynamic>;

  Future<Map<String, dynamic>> assays() async =>
      (await get('/colorimetry/assays')) as Map<String, dynamic>;

  Future<Map<String, dynamic>> meta() async =>
      (await get('/meta')) as Map<String, dynamic>;

  // --- Scans --------------------------------------------------------------
  Future<Map<String, dynamic>> createScan(Map<String, dynamic> payload) async =>
      (await post('/scans', payload)) as Map<String, dynamic>;

  Future<Map<String, dynamic>> syncScans(List<Map<String, dynamic>> scans) async =>
      (await post('/scans/sync', <String, dynamic>{'scans': scans}))
          as Map<String, dynamic>;

  Future<Map<String, dynamic>> attachReading(
    String scanId,
    Map<String, dynamic> reading,
  ) async =>
      (await post('/colorimetry/scans/$scanId/readings', reading))
          as Map<String, dynamic>;

  Future<Map<String, dynamic>> shareScan(String scanId, {bool shared = true}) async =>
      (await post('/scans/$scanId/share', null, <String, dynamic>{'shared': shared}))
          as Map<String, dynamic>;

  // --- Watch and Trace ----------------------------------------------------
  Future<Map<String, dynamic>> hotspots({int windowDays = 30}) async =>
      (await get('/hotspots', <String, dynamic>{'window_days': windowDays}))
          as Map<String, dynamic>;

  Future<Map<String, dynamic>> traceByQr(String token) async =>
      (await get('/trace/$token')) as Map<String, dynamic>;

  // --- Complaints ---------------------------------------------------------
  Future<Map<String, dynamic>> createComplaint(Map<String, dynamic> payload) async =>
      (await post('/complaints', payload)) as Map<String, dynamic>;

  Future<List<dynamic>> complaints() async => (await get('/complaints')) as List<dynamic>;

  void close() => _http.close();
}
