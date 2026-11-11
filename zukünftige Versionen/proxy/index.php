<?php
/**
 * index.php – Live-Traffic-Relay (Proxy) für MSFS Live Traffic Utility
 * =========================================================================
 * Ein Sammler-PC (ltu_collector.exe) pusht ADS-B-Daten asynchron ausschliesslich
 * fuer grob gerasterte Bereiche mit aktuellen Client-Traffic-Anfragen. Ohne
 * Nachfrage pausieren alle Upstream-Abfragen. Eine globale OpenSky-Abfrage wird
 * nur durch eine explizit abgesendete Client-Suche ausgelöst; OAuth2 bleibt auf
 * dem Collector-PC. Clients fragen ansonsten nur den sichtbaren Ausschnitt ab.
 *
 * Upload-Ziel:  http://dnd.59-seconds.com/ltu/index.php
 *               (diese Datei selbst ist der Index; der Ordner-Aufruf
 *                http://dnd.59-seconds.com/ltu/ zeigt die Statusseite)
 *
 * Endpunkte:
 *   POST ?action=push&region=ID&lat=..&lon=..&radius=..
 *        Header X-LTU-Token; Body = adsb ac[] oder OpenSky states[]
 *        (X-LTU-Source-Format: opensky loest die Normalisierung aus)
 *   POST ?action=source_status&region=ID         Collector meldet Fetch-Fehler
 *        Header X-LTU-Token, JSON mit HTTP, Modus und freigegebenem Quellnamen
 *                                               (keine Upstream-Credentials/Tokens)
 *   GET  ?action=traffic&bbox=N,S,E,W          Bereichs-Filter (Preferred)
 *   GET  ?action=traffic&lat=..&lon=..&dist=.. Kreis-Filter (kompatibel)
 *   GET  ?action=status                        Regions-Status + Push-Log
 *   GET  ?action=console                       Web-Konsole (Dashboard+Karte,
 *                                              Push-Chart + Stunden-Replay)
 *   GET  ?action=hist&region=ID&ts=UNIXTS      Replay-Snapshot (Token Pflicht)
 *   GET  ?action=track&hex=ICAOHEX|flight=ID   Letzte Stunde Flugzeug-Spur
 *   GET  (ohne ?action) = ?action=statuspage   OEFFENTLICHE Statusseite fuer User:
 *                                              welcher Teil laeuft / ist down,
 *                                              inkl. Ersatz-Proxy (Web-Relay).
 *                                              Diese PHP als index.php hochladen,
 *                                              dann IST http://…/ltu/ direkt die
 *                                              Statusseite (Default-Action)
 *   POST ?action=logout                        Client-Abmeldung (JSON {uid}),
 *                                              entfernt ihn sofort aus der Statusseite
 *   POST ?action=heartbeat                     Client-Status + MESH-WAHL (JSON,
 *                                              ohne Token, 1/2s pro UID): ist der
 *                                              Proxy down (Collector stale ODER
 *                                              Mehrheit meldet Fehler/HTTP 429),
 *                                              waehlt der Server ZUFALLIG einen
 *                                              Ersatz-Proxy; Antwort = Rolle
 *   POST ?action=relay_push                    Ersatz-Proxy schiebt seine Traffic-
 *                                              Daten aufs Web-Relay (nur gewaehlte UID)
 *   GET  ?action=relay_pull&bbox=..|lat&lon&dist
 *                                              Clients holen Traffic vom Web-Relay
 *                                              => alles ueber die Webseite, KEINE
 *                                              Ports/Router-/Firewall-Konfiguration
 *   GET  ?action=healthdata                    Status-Rohdaten (JSON, oeffentlich)
 *   GET  ?action=demand                        Client-Bereiche + globale Suchanfrage (Token)
 *   GET  ?action=global_search                 OpenSky-Snapshot anfordern/abrufen (oeffentlich)
 *                                              nur nach expliziter Suche; OAuth bleibt beim Collector
 *
 * Speicherung: pro Region eine gzippte JSON-Datei (ltu_r_<id>.json.gz) plus
 * Index (ltu_index.json) mit File-Lock – kein Datenbank-Setup nötig.
 * client_demand.php speichert nur kurzlebige, grob gerasterte Bereiche; ohne
 * aktuelle Traffic-Anfragen pausiert der Collector. Die exakten Client-Bereiche
 * werden nicht in den oeffentlichen Statusdaten ausgegeben.
 * Mesh: Wahl-Zustand in ltu_mesh.json, Relay-Daten in ltu_relay.json,
 * Heartbeats in ltu_clients.json (alle mit File-Lock, selbstpflegend).
 * History: alle LTU_HIST_INTERVAL Sekunden ein abgespeckter Snapshot pro Region
 * (ltu_h_<id>_<ts>.json.gz), ~LTU_HIST_KEEP Minuten, automatisch bereinigt.
 * Flugspuren: pro ICAO-Hex komprimierte Positionen aus Collector-/Relay-Pushes
 * und frischen Traffic-Abfragen; ein-Stunden-TTL, ausserhalb des Webroots.
 * Collector-Quelle: aktuelle Fetch-Fehler/Erfolge in ltu_source_status.json.
 * Hinweis: Das Verzeichnis muss für den Webserver beschreibbar sein.
 */

// --------------------------- KONFIGURATION ---------------------------
define('LTU_TOKEN',   'CHANGE-ME-BITTE');   // <-- gleiches Token wie im Collector!
define('LTU_MAX_AGE', 30);                  // Sekunden, bis Regions-Daten als stale gelten
define('LTU_MAX_AC',  30000);               // Schutz vor Endlos-Payloads pro Push
define('LTU_LOG_LEN', 40);                  // Länge des Push-Logs
define('LTU_INDEX',   __DIR__ . '/ltu_index.json');
define('LTU_HIST_INTERVAL', 60);            // Replay-Snapshot alle 60 s
define('LTU_HIST_KEEP', 90);                // Snapshots je Region (~90 min)
define('LTU_HIST_MAX_AC', 5000);            // Snapshots sind abgespeckt (Position+Höhe)
define('LTU_CLIENTS',  __DIR__ . '/ltu_clients.json'); // Heartbeats der Clients
define('LTU_CLIENT_TTL', 600);              // s, danach gilt ein Client als weg
define('LTU_MESH_F',   __DIR__ . '/ltu_mesh.json');   // Wahl-Zustand (Ersatz-Proxy)
define('LTU_RELAY',    __DIR__ . '/ltu_relay.json');  // Web-Relay: Daten des Ersatz
define('LTU_RELAY_MAX_AGE', 25);            // s, danach gilt das Relay als stale
define('LTU_SOURCE_STATUS', __DIR__ . '/ltu_source_status.json'); // Collector-Quelle
define('LTU_SOURCE_FRESH', 90);              // s, danach wird die Quelle als stale angezeigt
define('LTU_CLIENT_FRESH', 90);             // s, Heartbeat gilt als frisch (Wahl/Matrix)
define('LTU_ERSATZ_STALE', 45);             // s ohne Heartbeat -> Ersatz gilt als down
define('LTU_YIELD_SEC', 120);               // s Kandidatur-Pause bei net_ok=false (429)
define('LTU_HB_NORMAL', 30);                // s Heartbeat-Intervall Normalbetrieb
define('LTU_HB_MESH', 10);                  // s Heartbeat-Intervall bei aktivem Mesh
require_once __DIR__ . '/client_demand.php';
require_once __DIR__ . '/aircraft_tracks.php';
define('LTU_DEMANDS', ltu_demand_store_path(LTU_TOKEN, __DIR__)); // privat in PHP-Temp
define('LTU_DEMAND_TTL', 20);                // s ohne Traffic-Abfrage -> Sammelbereich laeuft ab
define('LTU_DEMAND_WRITE_INTERVAL', 5);      // max. eine Dateiaktualisierung je Bereich/5 s
define('LTU_DEMAND_MAX_REGIONS', 64);        // Obergrenze aktiver Sammelbereiche
define('LTU_GLOBAL_SEARCH_DEMAND', ltu_global_search_store_path(LTU_TOKEN, __DIR__));
define('LTU_GLOBAL_SEARCH_TTL', 180);          // nur waehrend einer aktiven Suche sammeln
define('LTU_GLOBAL_SEARCH_MIN_INTERVAL', 120); // OpenSky-Quota: zentral max. alle 120 s
define('LTU_GLOBAL_SEARCH_CACHE_TTL', 180);    // frischen globalen OpenSky-Snapshot wiederverwenden
// ---------------------------------------------------------------------

ini_set('memory_limit', '512M');
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Headers: X-LTU-Token, Content-Type');
header('Cache-Control: no-store');

$action = isset($_GET['action']) ? $_GET['action'] : 'statuspage';

function ltu_out($payload, $code = 200) {
    http_response_code($code);
    echo json_encode($payload);
    exit;
}

function ltu_html($html) {
    header('Content-Type: text/html; charset=utf-8');
    echo $html;
    exit;
}

function ltu_token_ok() {
    $tok = isset($_SERVER['HTTP_X_LTU_TOKEN']) ? $_SERVER['HTTP_X_LTU_TOKEN']
         : (isset($_GET['token']) ? $_GET['token']
         : (isset($_COOKIE['ltu_token']) ? $_COOKIE['ltu_token'] : ''));
    return is_string($tok) && $tok !== '' && hash_equals(LTU_TOKEN, $tok);
}

function ltu_haversine_km($lat1, $lon1, $lat2, $lon2) {
    $r = 6371.0;
    $p1 = deg2rad($lat1); $p2 = deg2rad($lat2);
    $dlat = deg2rad($lat2 - $lat1);
    $dlon = deg2rad($lon2 - $lon1);
    $a = sin($dlat / 2) ** 2 + cos($p1) * cos($p2) * sin($dlon / 2) ** 2;
    return 2 * $r * asin(min(1.0, sqrt($a)));
}

function ltu_num($key, $def = 0.0) {
    return isset($_GET[$key]) && is_scalar($_GET[$key]) ? floatval($_GET[$key]) : $def;
}

function ltu_region_max_age($region) {
    if (isset($region['source_max_age']) && is_numeric($region['source_max_age'])
        && intval($region['source_max_age']) > 0) {
        return min(86400, max(5, intval($region['source_max_age'])));
    }
    return LTU_MAX_AGE;
}

function ltu_source_max_age($source) {
    if (isset($source['max_age']) && is_numeric($source['max_age'])
        && intval($source['max_age']) > 0) {
        return min(86400, max(5, intval($source['max_age'])));
    }
    return LTU_SOURCE_FRESH;
}

// OpenSky /states/all: konvertiert die State-Vectoren in das interne ac-Format.
function ltu_opensky_to_adsb($payload) {
    if (!is_array($payload) || !array_key_exists('states', $payload)
        || ($payload['states'] !== null && !is_array($payload['states']))) return null;
    $out = array();
    foreach ((array)$payload['states'] as $state) {
        if (!is_array($state) || count($state) < 17
            || !isset($state[5], $state[6])
            || !is_numeric($state[5]) || !is_numeric($state[6])) continue;
        $lat = floatval($state[6]);
        $lon = floatval($state[5]);
        if ($lat < -90 || $lat > 90 || $lon < -180 || $lon > 180) continue;
        $on_ground = !empty($state[8]);
        $baro_alt_m = (isset($state[7]) && is_numeric($state[7]))
                    ? $state[7] : null;
        $geo_alt_m = (isset($state[13]) && is_numeric($state[13]))
                   ? $state[13] : null;
        $alt_baro = $baro_alt_m !== null
                  ? intval(round(floatval($baro_alt_m) * 3.28084))
                  : ($on_ground ? 'ground' : null);
        $alt_geom = $geo_alt_m !== null
                  ? intval(round(floatval($geo_alt_m) * 3.28084)) : null;
        $flight = isset($state[1]) && is_scalar($state[1])
                ? strtoupper(trim((string)$state[1])) : '';
        $ac = array(
            'hex' => isset($state[0]) ? strtolower(trim((string)$state[0])) : '',
            'flight' => $flight,
            'lat' => $lat,
            'lon' => $lon,
            'alt_baro' => $alt_baro,
            'alt_geom' => $alt_geom,
            'gs' => isset($state[9]) && is_numeric($state[9])
                  ? round(floatval($state[9]) * 1.94384, 1) : null,
            'track' => isset($state[10]) && is_numeric($state[10])
                     ? round(floatval($state[10]), 1) : null,
            'baro_rate' => isset($state[11]) && is_numeric($state[11])
                         ? intval(round(floatval($state[11]) * 196.85)) : null,
            'squawk' => isset($state[14]) && is_scalar($state[14])
                      ? (string)$state[14] : null,
            'src' => 'opensky',
        );
        if ($ac['hex'] === '') continue;
        $out[] = $ac;
        if (count($out) >= LTU_MAX_AC) break;
    }
    return array('ac' => $out);
}

function ltu_region_file($id) {
    return __DIR__ . '/ltu_r_' . $id . '.json.gz';
}

function ltu_hist_file($id, $ts) {
    return __DIR__ . '/ltu_h_' . $id . '_' . intval($ts) . '.json.gz';
}

function ltu_load_hist($id, $ts) {
    $f = ltu_hist_file($id, $ts);
    if (!is_file($f)) return null;
    $raw = @file_get_contents($f);
    if ($raw === false) return null;
    $j = json_decode(@gzdecode($raw), true);
    return is_array($j) ? $j : null;
}

function ltu_hist_prune($id, $keep_ts) {
    $keep = array();
    foreach ((array)$keep_ts as $t) $keep[intval($t)] = 1;
    foreach ((array)glob(__DIR__ . '/ltu_h_' . $id . '_*.json.gz') as $f) {
        if (preg_match('/_(\d+)\.json\.gz$/', $f, $m) && !isset($keep[intval($m[1])])) {
            @unlink($f);
        }
    }
}

function ltu_load_clients() {
    if (!is_file(LTU_CLIENTS)) return array();
    $fp = @fopen(LTU_CLIENTS, 'rb');
    if (!$fp) return array();
    flock($fp, LOCK_SH);
    $raw = stream_get_contents($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    $cl = json_decode($raw, true);
    return is_array($cl) ? $cl : array();
}

function ltu_clients_update($mutate) {
    $fp = @fopen(LTU_CLIENTS, 'c+b');
    if (!$fp) return false;
    flock($fp, LOCK_EX);
    $raw = stream_get_contents($fp);
    $cl = json_decode($raw, true);
    if (!is_array($cl)) $cl = array();
    $mutate($cl);
    ftruncate($fp, 0);
    rewind($fp);
    fwrite($fp, json_encode($cl));
    fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    return true;
}

// Generischer JSON-Store mit File-Lock (Mesh-Wahl + Web-Relay)
function ltu_json_load($f) {
    if (!is_file($f)) return array();
    $fp = @fopen($f, 'rb');
    if (!$fp) return array();
    flock($fp, LOCK_SH);
    $raw = stream_get_contents($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    $d = json_decode($raw, true);
    return is_array($d) ? $d : array();
}

function ltu_json_update($f, $mutate) {
    $fp = @fopen($f, 'c+b');
    if (!$fp) return false;
    flock($fp, LOCK_EX);
    $raw = stream_get_contents($fp);
    $d = json_decode($raw, true);
    if (!is_array($d)) $d = array();
    $mutate($d);
    ftruncate($fp, 0);
    rewind($fp);
    fwrite($fp, json_encode($d));
    fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    return true;
}

function ltu_source_status_update($region, $ok, $http, $mode = null,
                                 $source = 'adsb.lol', $max_age = 0) {
    $fp = @fopen(LTU_SOURCE_STATUS, 'c+b');
    if (!$fp) return false;
    if (!flock($fp, LOCK_EX)) {
        fclose($fp);
        return false;
    }
    $raw = stream_get_contents($fp);
    $d = json_decode($raw === false ? '' : $raw, true);
    if (!is_array($d)) $d = array();
    $now = time();
    foreach ($d as $id => $entry) {
        if (!is_array($entry) || !isset($entry['t'])
            || ($now - intval($entry['t'])) > 86400) unset($d[$id]);
    }
    if (!ltu_source_name_is_valid($source)) $source = 'adsb.lol';
    if ($mode === 'global' && $source !== 'opensky') return false;
    $entry = array('source' => $source, 'ok' => (bool)$ok,
                   'http' => intval($http), 't' => $now);
    if ($mode !== null) $entry['mode'] = (string)$mode;
    if (is_numeric($max_age) && intval($max_age) > 0) {
        $entry['max_age'] = min(86400, max(5, intval($max_age)));
    }
    $d[$region] = $entry;
    $json = json_encode($d);
    $written = false;
    if ($json !== false && ftruncate($fp, 0) && rewind($fp)) {
        $written = fwrite($fp, $json) === strlen($json) && fflush($fp);
    }
    flock($fp, LOCK_UN);
    fclose($fp);
    return $written;
}

function ltu_collector_online($idx, $now) {
    foreach ((array)$idx['regions'] as $r) {
        if (($now - intval($r['t'])) <= ltu_region_max_age($r)) return true;
    }
    return false;
}

// Serverseitige, ZUFALLSbasierte Ersatz-Proxy-Wahl mit Ketten-Failover.
// $cl = Clients-Map (uid => Heartbeat), $mesh = Wahl-Zustand (by reference).
// Retour: array(mesh_active, ersatz_uid, ersatz_name, hb_intervall)
function ltu_mesh_decide(&$cl, &$mesh, $collector_online, $now) {
    $mesh = array_merge(array('ersatz_uid' => '', 'elected_at' => 0, 'yields' => array()),
                        is_array($mesh) ? $mesh : array());
    $fresh = array();
    foreach ($cl as $uid => $c) {
        if (is_array($c) && isset($c['t']) && ($now - intval($c['t'])) <= LTU_CLIENT_FRESH) {
            $fresh[$uid] = $c;
        }
    }
    $down = 0;
    foreach ($fresh as $c) { if (empty($c['proxy_ok'])) $down++; }
    // Proxy down = Collector liefert nichts Frisches ODER die Mehrheit der
    // frischen Clients meldet proxy_ok=false (Fehler oder HTTP 429).
    $unhealthy = !$collector_online || (count($fresh) > 0 && $down * 2 >= count($fresh));
    if (!$unhealthy) {
        $mesh['ersatz_uid'] = '';
        $mesh['elected_at'] = 0;
        $mesh['yields'] = array();
        return array(false, '', null, LTU_HB_NORMAL);
    }
    // Yield: wer net_ok=false meldet (429-Sturm auf Direktquellen), pausiert.
    $yields = array();
    foreach ((array)$mesh['yields'] as $u => $t) { if (intval($t) > $now) $yields[$u] = intval($t); }
    foreach ($fresh as $uid => $c) { if (empty($c['net_ok'])) $yields[$uid] = $now + LTU_YIELD_SEC; }
    $mesh['yields'] = $yields;
    // Kandidaten: frisch, Direktquellen ok, nicht im Yield.
    $cands = array();
    foreach ($fresh as $uid => $c) {
        if (!empty($c['net_ok']) && !isset($yields[$uid])) $cands[] = $uid;
    }
    sort($cands);
    $cur = (string)$mesh['ersatz_uid'];
    $curOk = ($cur !== '' && isset($fresh[$cur]) && !empty($fresh[$cur]['net_ok'])
              && !isset($yields[$cur])
              && ($now - intval($fresh[$cur]['t'])) <= LTU_ERSATZ_STALE);
    if (!$curOk) {
        // Ketten-Failover: naechster ZUFAELLIGER Kandidat uebernimmt.
        if (count($cands) > 0) {
            $cur = (string)$cands[array_rand($cands)];
            $mesh['ersatz_uid'] = $cur;
            $mesh['elected_at'] = $now;
        } else {
            $cur = '';
            $mesh['ersatz_uid'] = '';
            $mesh['elected_at'] = 0;
        }
    }
    $name = ($cur !== '' && isset($fresh[$cur])) ? $fresh[$cur]['name'] : null;
    return array(true, $cur, $name, LTU_HB_MESH);
}

function ltu_is_legacy_global_fallback_region($id, $region) {
    return (is_array($region) && !empty($region['global_fallback']))
        || preg_match('/^GF[0-9]{4}$/D', (string)$id) === 1;
}

function ltu_prune_legacy_global_fallback_regions(&$regions) {
    foreach ((array)$regions as $id => $region) {
        if (ltu_is_legacy_global_fallback_region($id, $region)) unset($regions[$id]);
    }
}

function ltu_prune_legacy_global_fallback_log(&$log) {
    foreach ((array)$log as $index => $entry) {
        if (is_array($entry) && isset($entry['region'])
            && preg_match('/^GF[0-9]{4}$/D', (string)$entry['region'])) {
            unset($log[$index]);
        }
    }
    $log = array_values((array)$log);
}

function ltu_load_index() {
    if (!is_file(LTU_INDEX)) return array('regions' => array(), 'log' => array());
    $fp = @fopen(LTU_INDEX, 'rb');
    if (!$fp) return array('regions' => array(), 'log' => array());
    flock($fp, LOCK_SH);
    $raw = stream_get_contents($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    $st = json_decode($raw, true);
    if (!is_array($st)) $st = array();
    if (!isset($st['regions']) || !is_array($st['regions'])) $st['regions'] = array();
    ltu_prune_legacy_global_fallback_regions($st['regions']);
    if (!isset($st['log']) || !is_array($st['log'])) $st['log'] = array();
    ltu_prune_legacy_global_fallback_log($st['log']);
    return $st;
}

function ltu_save_index($st) {
    $fp = @fopen(LTU_INDEX, 'c+b');
    if (!$fp) return false;
    flock($fp, LOCK_EX);
    ftruncate($fp, 0);
    rewind($fp);
    fwrite($fp, json_encode($st));
    fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    return true;
}

function ltu_index_update($mutate) {
    // Read-Modify-Write unter exklusivem Lock: parallele Region-Pushes
    // duerfen sich nicht gegenseitig ueberschreiben.
    $fp = @fopen(LTU_INDEX, 'c+b');
    if (!$fp) return false;
    flock($fp, LOCK_EX);
    $raw = stream_get_contents($fp);
    $st = json_decode($raw, true);
    if (!is_array($st)) $st = array();
    if (!isset($st['regions']) || !is_array($st['regions'])) $st['regions'] = array();
    ltu_prune_legacy_global_fallback_regions($st['regions']);
    if (!isset($st['log']) || !is_array($st['log'])) $st['log'] = array();
    ltu_prune_legacy_global_fallback_log($st['log']);
    $mutate($st);
    ftruncate($fp, 0);
    rewind($fp);
    fwrite($fp, json_encode($st));
    fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    return true;
}

function ltu_load_region($id) {
    $f = ltu_region_file($id);
    if (!is_file($f)) return null;
    $raw = @file_get_contents($f);
    if ($raw === false) return null;
    $json = @gzdecode($raw);
    if ($json === false) return null;
    $d = json_decode($json, true);
    return is_array($d) ? $d : null;
}

// Kreis (lat/lon/radius_km) -> Bounding Box. Laengengrade duerfen ueber
// +/-180 hinauslaufen; die Schnitt- und Punktfilter unten wickeln sie herum.
function ltu_bbox_from_circle($lat, $lon, $km) {
    $dlat = $km / 111.0;
    $dlon = $km / max(1.0, 111.0 * cos(deg2rad($lat)));
    if ($dlon >= 180.0) { $west = -180.0; $east = 180.0; }
    else { $west = $lon - $dlon; $east = $lon + $dlon; }
    return array(min(90, $lat + $dlat), max(-90, $lat - $dlat),
                 $east, $west); // n,s,e,w
}

function ltu_lon_segments($west, $east) {
    if ($east < $west && $west >= -180.0 && $west <= 180.0
        && $east >= -180.0 && $east <= 180.0) {
        return array(array($west, 180.0), array(-180.0, $east));
    }
    $width = $east - $west;
    if ($width >= 360.0) return array(array(-180.0, 180.0));
    while ($west < -180.0) { $west += 360.0; $east += 360.0; }
    while ($west > 180.0) { $west -= 360.0; $east -= 360.0; }
    if ($east > 180.0) {
        return array(array($west, 180.0), array(-180.0, $east - 360.0));
    }
    if ($west < -180.0) {
        return array(array(-180.0, $east), array($west + 360.0, 180.0));
    }
    $segments = array(array($west, $east));
    if ($east == 180.0 && $west > -180.0) $segments[] = array(-180.0, -180.0);
    if ($west == -180.0 && $east < 180.0) $segments[] = array(180.0, 180.0);
    return $segments;
}

function ltu_bbox_intersects($a, $b) {
    // a/b = [n,s,e,w], inkl. Bounding Boxes ueber dem Antimeridian.
    if ($a[1] > $b[0] || $b[1] > $a[0]) return false;
    $aseg = ltu_lon_segments(floatval($a[3]), floatval($a[2]));
    $bseg = ltu_lon_segments(floatval($b[3]), floatval($b[2]));
    foreach ($aseg as $x) foreach ($bseg as $y) {
        if (!($x[0] > $y[1] || $y[0] > $x[1])) return true;
    }
    return false;
}

function ltu_lon_in_bbox($lon, $bbox) {
    $lon = fmod(floatval($lon) + 180.0, 360.0);
    if ($lon < 0.0) $lon += 360.0;
    $lon -= 180.0;
    foreach (ltu_lon_segments(floatval($bbox[3]), floatval($bbox[2])) as $seg) {
        if ($lon >= $seg[0] && $lon <= $seg[1]) return true;
    }
    return false;
}

// ----------------------------- KONSOLE -----------------------------
if ($action === 'console') {
    if (!ltu_token_ok()) {
        $hint = isset($_GET['token']) ? '<p style="color:#e94560;margin:0 0 12px">Token falsch – bitte erneut versuchen.</p>' : '';
        ltu_html('<!DOCTYPE html><html><head><meta charset="utf-8"><title>LTU Proxy Login</title>
        <style>body{background:#10131c;color:#dfe6f3;font-family:system-ui;display:grid;place-items:center;height:100vh}
        form{background:#1b2130;padding:28px 36px;border-radius:10px;border:1px solid #2a3245}
        input{background:#0f3460;border:1px solid #2a3245;color:#fff;padding:8px;border-radius:6px;margin-right:8px}
        button{background:#e94560;color:#fff;border:0;padding:8px 18px;border-radius:6px;cursor:pointer}</style></head>
        <body><form method="get"><input type="hidden" name="action" value="console">' . $hint . '
        <input type="password" name="token" placeholder="Proxy-Token" autofocus>
        <button>Anmelden</button></form></body></html>');
    }
    $tok_keep = isset($_GET['token']) ? $_GET['token']
              : (isset($_COOKIE['ltu_token']) ? $_COOKIE['ltu_token'] : '');
    if ($tok_keep !== '') setcookie('ltu_token', $tok_keep, time() + 86400);
    $idx = ltu_load_index();
    ltu_html(ltu_console_html($idx));
}

function ltu_console_html($idx) {
    $console_regions = array();
    foreach ($idx['regions'] as $id => $region) {
        $console_regions[$id] = $region;
    }
    $regions_json = json_encode($console_regions);
    $log_json = json_encode($idx['log']);
    $tok = isset($_GET['token']) ? htmlspecialchars($_GET['token'], ENT_QUOTES)
         : (isset($_COOKIE['ltu_token']) ? htmlspecialchars($_COOKIE['ltu_token'], ENT_QUOTES) : '');
    return '<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<title>LTU Proxy Konsole</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{--bg:#10131c;--panel:#1b2130;--line:#2a3245;--fg:#dfe6f3;--mut:#8b93a7;--acc:#4da3ff;--ok:#3ddc84;--bad:#ff5252}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 "Segoe UI",system-ui,sans-serif}
header{display:flex;align-items:center;gap:14px;padding:12px 18px;background:var(--panel);border-bottom:1px solid var(--line)}
header h1{font-size:16px;margin:0;font-weight:600}
header .dot{width:10px;height:10px;border-radius:50%;background:var(--bad)}
header .dot.ok{background:var(--ok)}
main{display:grid;grid-template-columns:1fr 380px;gap:14px;padding:14px;height:calc(100vh - 52px)}
#map{border-radius:10px;border:1px solid var(--line);min-height:300px}
.side{display:flex;flex-direction:column;gap:14px;overflow:auto}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card h2{margin:0 0 8px 0;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut)}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.kpi b{display:block;font-size:20px}
.kpi span{color:var(--mut);font-size:11px}
table{width:100%;border-collapse:collapse;font-size:12px}
td,th{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{color:var(--mut);font-weight:600}
.reg{cursor:pointer}
.reg:hover td{background:#232a3d}
.badge{padding:1px 8px;border-radius:10px;font-size:11px;background:#22304a;color:var(--acc)}
.badge.stale{background:#3a2230;color:var(--bad)}
#logwrap{overflow:auto;max-height:220px}
.row{display:flex;gap:8px;align-items:center;margin-bottom:6px}
select,button{background:#0f3460;color:#dfe6f3;border:1px solid #2a3245;border-radius:6px;padding:4px 10px;cursor:pointer;font:inherit}
button:hover{border-color:var(--acc)}
input[type=range]{width:100%;accent-color:var(--acc)}
.mut{color:var(--mut);font-size:11px}
#chart{margin-top:8px}
</style></head><body>
<header><div class="dot" id="dot"></div><h1>LTU Proxy &ndash; Konsole</h1>
<span class="mut" id="upd"></span>
<a href="?action=statuspage" target="_blank" style="color:var(--acc);font-size:12px;margin-left:auto;text-decoration:none">Statusseite (f&uuml;r User) &#8599;</a></header>
<div class="kpis" style="padding:14px 14px 0">
<div class="kpi"><b id="kRegions">&ndash;</b><span>Regionen online</span></div>
<div class="kpi"><b id="kAc">&ndash;</b><span>Flugzeuge gesamt</span></div>
<div class="kpi"><b id="kAge">&ndash;</b><span>&Auml;lteste Region (s)</span></div>
<div class="kpi"><b id="kPush">&ndash;</b><span>Pushes (Log)</span></div>
</div>
<main>
<div id="map"></div>
<div class="side">
  <div class="card"><h2>Regionen <span class="mut">(Klick = Live-Ansicht)</span></h2><div id="regions"></div></div>
  <div class="card"><h2>Replay &amp; Pushes</h2>
    <div class="row">
      <select id="rRegion" style="flex:1"></select>
      <button id="rPlay" title="Replay starten">&#9654;</button>
    </div>
    <input type="range" id="rSlider" min="0" max="0" value="0">
    <div class="row"><span class="mut" id="rTime"></span></div>
    <div id="chart"></div>
  </div>
  <div class="card"><h2>Push-Log</h2><div id="logwrap"><table id="log"></table></div></div>
</div>
</main>
<script>
const TOKEN = "' . $tok . '";
let REGIONS = ' . $regions_json . ';
let LOG = ' . $log_json . ';
const MAXAGE = ' . LTU_MAX_AGE . ';

const map = L.map("map", {worldCopyJump:true}).setView([30,10], 2);
L.tileLayer("https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png", {maxZoom:19, attribution:""}).addTo(map);
const rectLayer = L.layerGroup().addTo(map);
const acLayer = L.layerGroup().addTo(map);
const replayLayer = L.layerGroup().addTo(map);
const $id = (x) => document.getElementById(x);

function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;"); }
function fmtTime(ts){ return new Date(ts*1000).toLocaleTimeString(); }
function histOf(id){ const r = REGIONS[id]; return (r && r.hist) ? r.hist : []; }

function renderBase(){
  const now = Math.floor(Date.now()/1000);
  const ids = Object.keys(REGIONS);
  let online=0, ac=0, oldest=0;
  rectLayer.clearLayers();
  const rows = [];
  ids.sort().forEach(id=>{
    const r = REGIONS[id];
    const age = now - r.t;
    const maxAge = Number(r.source_max_age || MAXAGE);
    const ok = age <= maxAge;
    if(ok){ online++; ac += r.count; }
    oldest = Math.max(oldest, age);
    if (!r.demand_driven && r.bbox) {
      const b = r.bbox;
      const rect = L.rectangle([[b[1], b[3]], [b[0], b[2]]],
         {color: ok ? "#3ddc84" : "#ff5252", weight:1, fillOpacity:0.06}).addTo(rectLayer);
      rect.bindTooltip(`${id} \u00b7 ${r.source||"?"} \u00b7 ${r.count} ac \u00b7 ${age}s`);
      rect.on("click", ()=>loadRegion(id));
    }
    rows.push(`<tr class="reg" data-id="${id}"><td>${esc(id)}</td><td>${esc(r.source||"-")}</td><td>${r.count}</td>` +
              `<td>${age}s</td><td><span class="badge${ok?"":" stale"}">${ok?"online":"stale"}</span></td></tr>`);
  });
  $id("kRegions").textContent = `${online}/${ids.length}`;
  $id("kAc").textContent = ac;
  $id("kAge").textContent = ids.length ? oldest : "\u2013";
  $id("kPush").textContent = LOG.length;
  $id("regions").innerHTML =
    "<table><tr><th>Region</th><th>Quelle</th><th>AC</th><th>Alter</th><th>Status</th></tr>" + rows.join("") + "</table>";
  $id("log").innerHTML =
    "<tr><th>Zeit</th><th>Region</th><th>AC</th><th>IP</th></tr>" +
    LOG.slice().reverse().map(l=>`<tr><td>${fmtTime(l.t)}</td><td>${esc(l.region)}</td>` +
        `<td>${l.count}</td><td>${esc(l.ip)}</td></tr>`).join("");
  $id("dot").className = "dot" + (online ? " ok" : "");
  $id("upd").textContent = "Stand " + fmtTime(now);
  document.querySelectorAll("tr.reg").forEach(tr=>{ tr.onclick = ()=>loadRegion(tr.dataset.id); });
}

function renderChart(){
  const el = $id("chart");
  if (!LOG.length){ el.innerHTML = ""; return; }
  const w=320, h=64, pad=4;
  const counts = LOG.map(l=>l.count||0);
  const maxc = Math.max.apply(null, counts.concat([1]));
  const t0 = LOG[0].t, t1 = LOG[LOG.length-1].t, dt = Math.max(1, t1-t0);
  const pts = LOG.map(l=>{
    const x = pad + (l.t-t0)/dt*(w-2*pad);
    const y = h-pad - ((l.count||0)/maxc)*(h-2*pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  el.innerHTML = `<div class="mut">Pushes/AC &uuml;ber Zeit (letzte ${LOG.length}) &middot; max ${maxc}</div>` +
    `<svg width="${w}" height="${h}" style="background:#141926;border-radius:6px;max-width:100%">` +
    `<polyline points="${pts}" fill="none" stroke="#4da3ff" stroke-width="2"/></svg>`;
}

function drawSnapshot(d){
  replayLayer.clearLayers();
  (d.ac||[]).forEach(a=>{
    if (a.lat==null || a.lon==null) return;
    const alt = (a.alt==="ground"||a.alt==null) ? 0 : (a.alt||0);
    const hue = Math.max(0, 200 - Math.min(45000, alt)/45000*200);
    L.circleMarker([a.lat,a.lon],{radius:2,weight:0,fillColor:`hsl(${hue},90%,60%)`,fillOpacity:0.85})
      .bindTooltip(String(a.flight||a.hex||"")).addTo(replayLayer);
  });
}

let replayIdx = 0, replayTimer = null, replaying = false;

function refreshRegionOptions(){
  const sel = $id("rRegion");
  const cur = sel.value;
  const ids = Object.keys(REGIONS).sort();
  sel.innerHTML = ids.map(i=>`<option value="${esc(i)}">${esc(i)} (${histOf(i).length})</option>`).join("");
  if (ids.indexOf(cur) >= 0) sel.value = cur;
  setupSlider();
}

function setupSlider(){
  const hist = histOf($id("rRegion").value);
  const sl = $id("rSlider");
  sl.max = Math.max(0, hist.length-1);
  if (replayIdx >= hist.length) replayIdx = Math.max(0, hist.length-1);
  sl.value = replayIdx;
  showTime();
}

function showTime(){
  const hist = histOf($id("rRegion").value);
  const ts = hist[replayIdx];
  $id("rTime").textContent = ts ? fmtTime(ts) : "keine Snapshots (erst nach ~1 min)";
}

function loadHist(){
  const id = $id("rRegion").value;
  const hist = histOf(id);
  const ts = hist[replayIdx];
  if (!ts){ replayLayer.clearLayers(); showTime(); return; }
  fetch(`?action=hist&region=${encodeURIComponent(id)}&ts=${ts}&token=${encodeURIComponent(TOKEN)}`)
    .then(r=>r.json()).then(d=>{
      drawSnapshot(d);
      $id("rTime").textContent = `${fmtTime(ts)} \u00b7 ${(d.ac||[]).length} ac`;
    }).catch(()=>{});
}

function stopReplay(){
  replaying = false;
  $id("rPlay").innerHTML = "&#9654;";
  if (replayTimer) clearInterval(replayTimer);
  replayTimer = null;
}

function startReplay(){
  const hist = histOf($id("rRegion").value);
  if (hist.length < 2) return;
  replaying = true;
  $id("rPlay").innerHTML = "&#10074;&#10074;";
  replayTimer = setInterval(()=>{
    replayIdx = (replayIdx + 1) % hist.length;
    $id("rSlider").value = replayIdx;
    loadHist();
  }, 1000);
}

function loadRegion(id){
  stopReplay();
  replayLayer.clearLayers();
  acLayer.clearLayers();
  const r = REGIONS[id];
  if (!r || r.demand_driven || !r.bbox) return;
  const b = r.bbox;
  fetch(`?action=traffic&bbox=${b.join(",")}&token=${encodeURIComponent(TOKEN)}`)
    .then(r=>r.json()).then(d=>{
      (d.ac||[]).forEach(a=>{
        if (a.lat==null || a.lon==null) return;
        const alt = (a.alt_baro==="ground"||a.alt_baro==null) ? 0 : (a.alt_baro||0);
        const hue = Math.max(0, 200 - Math.min(45000, alt)/45000*200);
        L.circleMarker([a.lat,a.lon],{radius:2,weight:0,fillColor:`hsl(${hue},90%,60%)`,fillOpacity:0.9})
          .bindTooltip(String(a.flight||a.hex||"")).addTo(acLayer);
      });
      map.fitBounds([[b[1],b[3]],[b[0],b[2]]]);
    }).catch(()=>{});
}

function refresh(){
  fetch(`?action=status&token=${encodeURIComponent(TOKEN)}`)
    .then(r=>r.json()).then(d=>{
      REGIONS = d.regions || {};
      LOG = d.log || [];
      renderBase();
      renderChart();
      refreshRegionOptions();
    }).catch(()=>{});
}

$id("rSlider").oninput = (e)=>{ replayIdx = parseInt(e.target.value,10)||0; showTime(); loadHist(); };
$id("rRegion").onchange = ()=>{
  stopReplay();
  const hist = histOf($id("rRegion").value);
  replayIdx = Math.max(0, hist.length-1);
  setupSlider();
  loadHist();
};
$id("rPlay").onclick = ()=>{ replaying ? stopReplay() : startReplay(); };

renderBase();
renderChart();
refreshRegionOptions();
setInterval(refresh, 5000);
</script></body></html>';
}

function ltu_statuspage_html() {
    return '<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LTU Proxy &ndash; Status</title>
<style>
:root{--bg:#10131c;--panel:#1b2130;--line:#2a3245;--fg:#dfe6f3;--mut:#8b93a7;--acc:#4da3ff;--ok:#3ddc84;--bad:#ff5252;--warn:#ffb020}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 "Segoe UI",system-ui,sans-serif;padding:0 0 40px}
header{display:flex;align-items:center;gap:14px;padding:14px 20px;background:var(--panel);border-bottom:1px solid var(--line)}
header h1{font-size:17px;margin:0;font-weight:600}
header .dot{width:11px;height:11px;border-radius:50%;background:var(--ok)}
header .dot.bad{background:var(--bad)}
.mut{color:var(--mut);font-size:12px}
main{padding:16px 20px;max-width:1100px;margin:0 auto}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:16px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi b{display:block;font-size:18px;margin-bottom:2px}
.kpi span{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.kpi b.ok{color:var(--ok)} .kpi b.bad{color:var(--bad)} .kpi b.warn{color:var(--warn)} .kpi b.unk{color:var(--mut)}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut);margin:18px 0 8px}
table{width:100%;border-collapse:collapse;font-size:12px;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{color:var(--mut);font-weight:600;background:#161b28}
td.c{text-align:center;font-weight:700}
td.c.ok{color:var(--ok)} td.c.bad{color:var(--bad)} td.c.warn{color:var(--warn)} td.c.unk{color:var(--mut)}
.badge{padding:1px 8px;border-radius:10px;font-size:11px;background:#22304a;color:var(--acc)}
.badge.er{background:#3a2230;color:var(--warn);font-weight:700}
.badge.stale{background:#3a2230;color:var(--bad)}
.uid{color:var(--mut);font-size:11px}
footer{max-width:1100px;margin:20px auto 0;padding:0 20px}
</style></head><body>
<header><div class="dot" id="hdot"></div><h1>LTU Proxy &ndash; Status</h1>
<span class="mut">Welcher Teil funktioniert gerade und welcher ist down &middot; Stand <span id="upd">?</span></span></header>
<main>
<div class="kpis">
  <div class="kpi"><b id="kProxy" class="ok">erreichbar</b><span>Proxy (diese Seite)</span></div>
  <div class="kpi"><b id="kColl">&ndash;</b><span>Collector / Regionen</span></div>
  <div class="kpi"><b id="kAc">&ndash;</b><span>Flugzeuge gesamt</span></div>
  <div class="kpi"><b id="kErsatz">&ndash;</b><span>Ersatz-Proxy aktiv</span></div>
  <div class="kpi"><b id="kClients">&ndash;</b><span>Clients melden sich</span></div>
  <div class="kpi"><b id="kDemand">&ndash;</b><span>Client-Nachfrage</span></div>
  <div class="kpi"><b id="kSource">&ndash;</b><span>Upstream-Quelle</span></div>
</div>
<h2>Clients &amp; Komponenten</h2>
<div id="clients"></div>
<h2>Collector-Quellen</h2>
<div id="sources"></div>
<h2>Collector-Regionen</h2>
<div id="regions"></div>
</main>
<footer class="mut">Auto-Refresh alle 5 s &middot; Daten: <code>?action=healthdata</code> &middot;
  Der Collector fragt adsb.lol, <a href="https://adsb.fi/" target="_blank" rel="noopener">adsb.fi</a>, danach OpenSky und zuletzt optional PlaneTrack.ai nur f&uuml;r aktuell angefragte Client-Bereiche ab; ohne aktive Nachfrage pausieren alle Upstreams. PlaneTrack-Zugangsdaten bleiben ausschlie&szlig;lich lokal auf dem Collector-PC und erscheinen nicht im Status. OpenSky ist nur f&uuml;r Forschung/nicht-kommerzielle Nutzung; seine Zugangsdaten bleiben ebenfalls lokal. Clients melden ihren Status per Heartbeat. F&auml;llt der Proxy aus (oder liefert HTTP 429), w&auml;hlt der Server zuf&auml;llig einen Ersatz-Proxy &ndash; bei dessen Ausfall den n&auml;chsten. Der Ersatz schiebt seine Daten auf diese Seite (Web-Relay), alle anderen holen sie dort: alles &uuml;ber die Webseite, ganz ohne Router-/Firewall-Konfiguration.</footer>
<script>
let DATA = null;
const COMPS = ["proxy","adsb.lol","adsb.fi","opensky","ersatz","msfs"];
const COMP_LABEL = {"adsb.lol":"lol","adsb.fi":"fi","opensky":"OSKY","ersatz":"Ersatz","proxy":"Proxy","msfs":"MSFS"};
function esc(s){ return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function cell(e){
  if (!e || e.ok == null) return `<td class="c unk" title="noch kein Bericht">&ndash;</td>`;
  if (e.ok) return `<td class="c ok" title="${esc(e.detail||"ok")}">&#10003;</td>`;
  if (e.code === 429) return `<td class="c warn" title="rate-limited, Cooldown aktiv">429</td>`;
  return `<td class="c bad" title="${esc(e.detail||"down")}">&#10007;</td>`;
}
function render(){
  if (!DATA) return;
  const p = DATA.proxy || {};
  const demand = DATA.demand || {};
  const activeRegions = Number(demand.active_regions || 0);
  const globalSearchPending = !!demand.global_search_pending;
  const demandPaused = !!demand.paused || (!activeRegions && !globalSearchPending);
  const globalSearchOnly = globalSearchPending && !activeRegions;
  const collectorOnline = !!p.collector_online;
  const collOk = collectorOnline || demandPaused || globalSearchPending;
  const srcs = DATA.source_status || {};
  const sids = Object.keys(srcs);
  const sourceMaxAge = Number(DATA.source_max_age || 90);
  const sourceLimit = s => Number((s && s.max_age) || sourceMaxAge);
  document.getElementById("hdot").className = "dot" + (collOk ? "" : " bad");
  document.getElementById("kColl").className = demandPaused ? "warn"
    : (globalSearchOnly && !collectorOnline ? "warn" : (collOk ? "ok" : "bad"));
  document.getElementById("kColl").textContent = demandPaused
    ? "pausiert · keine Nachfrage"
    : (globalSearchOnly && !collectorOnline
      ? "globale OpenSky-Suche läuft"
      : `${p.regions_online||0}/${p.regions_total||0} online`);
  const demandKpi = document.getElementById("kDemand");
  if (demandPaused) {
    demandKpi.className = "warn";
    demandKpi.textContent = "pausiert · keine Nachfrage";
  } else if (globalSearchOnly) {
    demandKpi.className = "warn";
    demandKpi.textContent = "globale OpenSky-Suche läuft";
  } else {
    demandKpi.className = "ok";
    demandKpi.textContent = `${activeRegions} Bereiche angefragt${globalSearchPending ? " · globale Suche aktiv" : ""}`;
  }
  document.getElementById("kAc").className = "";
  document.getElementById("kAc").textContent = p.total_ac || 0;

  const sourceKpi = document.getElementById("kSource");
  if (demandPaused) {
    sourceKpi.className = "warn";
    sourceKpi.textContent = "pausiert ohne Client-Nachfrage";
  } else if (globalSearchOnly && globalSearchPending) {
    sourceKpi.className = "warn";
    sourceKpi.textContent = "OpenSky-Suche angefordert";
  } else if (!sids.length) {
    sourceKpi.className = "unk";
    sourceKpi.textContent = "keine Meldung";
  } else {
    const latestId = sids.reduce((best, id) =>
      !best || Number(srcs[id].t || 0) > Number(srcs[best].t || 0) ? id : best, "");
    const latest = srcs[latestId] || {};
    const sourceAge = Math.max(0, Number(DATA.now) - Number(latest.t || 0));
    if (sourceAge > sourceLimit(latest)) {
      sourceKpi.className = "warn";
      sourceKpi.textContent = "Meldung veraltet";
    } else if (latest.ok) {
      sourceKpi.className = "ok";
      sourceKpi.textContent = `erreichbar (${latestId})`;
    } else {
      sourceKpi.className = "bad";
      sourceKpi.textContent = latest.http ? `nicht erreichbar (HTTP ${latest.http})` : "keine HTTP-Antwort";
    }
  }
  const sourceRows = sids.sort().map(id => {
    const s = srcs[id] || {};
    const age = Math.max(0, Number(DATA.now) - Number(s.t || 0));
    const fresh = age <= sourceLimit(s);
    const label = !fresh ? "veraltet" : (s.ok ? "erreichbar" : (s.http ? `HTTP ${s.http}` : "keine HTTP-Antwort"));
    const cls = !fresh ? "warn" : (s.ok ? "ok" : "bad");
    const service = s.source === "opensky" ? "OpenSky OAuth2"
      : (s.source === "planetrack" ? "PlaneTrack.ai (Collector)"
      : (s.source === "adsb.fi" ? "adsb.fi (kostenlos)" : "adsb.lol (kostenlos)"));
    const mode = s.mode === "global" ? "globale OpenSky-Suche"
      : (s.mode === "demand" ? "aktive Client-Bereiche"
      : (s.mode === "regional" ? "Regionen" : "-"));
    return `<tr><td>${esc(service)}</td><td>${esc(id)}</td><td class="c ${cls}">${esc(label)}</td>` +
      `<td>${s.http ? esc(s.http) : "&ndash;"}</td><td>${age}s</td><td>${esc(mode)}</td></tr>`;
  }).join("");
  document.getElementById("sources").innerHTML =
    `<table><tr><th>Dienst</th><th>Quelle</th><th>Status</th><th>HTTP</th><th>Alter</th><th>Modus</th></tr>` +
    (sourceRows || `<tr><td class="mut" colspan="6">Noch keine Meldung vom Collector</td></tr>`) + `</table>`;

  const ek = document.getElementById("kErsatz");
  const mesh = DATA.mesh || {};
  if (mesh.active && DATA.ersatz) {
    ek.className = "warn";
    ek.textContent = DATA.ersatz + (mesh.relay_age != null ? ` \u00b7 Web-Relay ${mesh.relay_age}s alt, ${mesh.relay_count||0} AC` : "");
  } else { ek.className = collOk ? "ok" : ""; ek.textContent = collOk ? "nicht n\u00f6tig" : "Wahl l\u00e4uft\u2026"; }
  const ids = Object.keys(DATA.clients||{});
  document.getElementById("kClients").textContent = ids.length;
  document.getElementById("upd").textContent = new Date(DATA.now*1000).toLocaleTimeString();

  const cl = ids.map(u=>DATA.clients[u]).sort((a,b)=>b.t-a.t);
  const head = `<tr><th>Client</th><th>UID</th><th>Rolle</th><th>Peers</th>` +
    COMPS.map(k=>`<th>${esc(COMP_LABEL[k]||k)}</th>`).join("") + `<th>zuletzt</th></tr>`;
  const rows = cl.map(c=>{
    const st = c.statuses || {};
    const role = c.role === "ersatz" ? `<span class="badge er">Ersatz-Proxy</span>`
      : (c.role === "candidate" ? `<span class="badge">Kandidat</span>` : `<span class="badge">Client</span>`);
    return `<tr><td>${esc(c.name)}</td><td class="uid">${esc(c.uid||"?")}</td><td>${role}</td>` +
      `<td>${c.peers||0}</td>` + COMPS.map(k=>cell(st[k])).join("") +
      `<td>${DATA.now - c.t}s</td></tr>`;
  }).join("");
  document.getElementById("clients").innerHTML =
    `<table>${head}${rows || `<tr><td class="mut" colspan="11">keine Heartbeats (Clients aus oder Proxy neu)</td></tr>`}</table>`;

  const rids = Object.keys(DATA.regions||{}).sort();
  const rrows = rids.map(id=>{
    const r = DATA.regions[id];
    const age = DATA.now - r.t;
    const ok = age <= Number(r.source_max_age || DATA.max_age);
    return `<tr><td>${esc(id)}</td><td>${esc(r.source||"-")}</td><td>${r.count||0}</td><td>${age}s</td>` +
      `<td><span class="badge${ok?"":" stale"}">${ok?"online":"stale"}</span></td></tr>`;
  }).join("");
  const emptyRegions = `<tr><td class="mut" colspan="5">noch keine Regionen gepusht</td></tr>`;
  document.getElementById("regions").innerHTML =
    `<table><tr><th>Region</th><th>Quelle</th><th>AC</th><th>Alter</th><th>Status</th></tr>` +
    (rrows || emptyRegions) + `</table>`;
}
function refresh(){
  fetch("?action=healthdata").then(r=>r.json()).then(d=>{ DATA = d; render(); }).catch(()=>{
    document.getElementById("kProxy").className = "bad";
  });
}
refresh();
setInterval(refresh, 5000);
</script></body></html>';
}

// ----------------------------- PUSH (Collector, asynchron pro Region) -----------------------------
if ($action === 'push') {
    if (!ltu_token_ok()) {
        ltu_out(array('error' => 'unauthorized'), 401);
    }
    $rid = (isset($_GET['region']) && is_scalar($_GET['region'])) ? (string)$_GET['region'] : 'global';
    $id = preg_replace('/[^A-Za-z0-9_\-]/', '', $rid);
    if ($id === '' || strlen($id) > 32) $id = 'global';
    if (isset($_GET['fallback']) || preg_match('/^GF[0-9]{4}$/D', $id)) {
        ltu_out(array('error' => 'legacy global-tile fallback is disabled'), 410);
    }

    $raw = file_get_contents('php://input');
    $data = json_decode($raw, true);
    $source_name = isset($_SERVER['HTTP_X_LTU_SOURCE_NAME'])
                 ? (string)$_SERVER['HTTP_X_LTU_SOURCE_NAME'] : 'adsb.lol';
    if (!ltu_source_name_is_valid($source_name)) $source_name = 'adsb.lol';
    $source_format = isset($_SERVER['HTTP_X_LTU_SOURCE_FORMAT'])
                   ? strtolower(trim((string)$_SERVER['HTTP_X_LTU_SOURCE_FORMAT'])) : 'adsb';
    if ($source_format === 'opensky' && $source_name === 'opensky') {
        $data = ltu_opensky_to_adsb($data);
    }
    if (!is_array($data) || !isset($data['ac']) || !is_array($data['ac'])) {
        ltu_out(array('error' => 'bad payload, expected adsb ac[] or OpenSky states[]'), 400);
    }
    $ac = array_slice($data['ac'], 0, LTU_MAX_AC);
    $track_records = ltu_track_record_batch($ac, time());
    ltu_track_prune_expired(time());
    $source_max_age = isset($_SERVER['HTTP_X_LTU_SOURCE_MAX_AGE'])
                    ? max(0, min(86400, intval($_SERVER['HTTP_X_LTU_SOURCE_MAX_AGE']))) : 0;

    $lat = ltu_num('lat');
    $lon = ltu_num('lon');
    $radius = ltu_num('radius');
    $bbox = ($id === 'global')
        ? array(90.0, -90.0, 180.0, -180.0)
        : ltu_bbox_from_circle($lat, $lon, max(1.0, $radius));

    $payload = json_encode(array(
        't'   => time(),
        'ac'  => $ac,
    ));
    $gz = gzencode($payload, 6);
    if (@file_put_contents(ltu_region_file($id), $gz, LOCK_EX) === false) {
        ltu_out(array('error' => 'write failed (permissions?)'), 500);
    }

    $meta = array(
        't'      => time(),
        'count'  => count($ac),
        'center' => array($lat, $lon),
        'radius' => $radius,
        'bbox'   => $bbox,
        'demand_driven' => (bool)preg_match('/^D[A-F0-9]{12}$/D', $id),
        'source' => $source_name,
        'source_max_age' => $source_max_age,
    );
    $logentry = array(
        't'      => time(),
        'region' => $id,
        'count'  => count($ac),
        'ip'     => isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : '?',
    );
    $hist_ts = null;
    ltu_index_update(function (&$idx) use ($id, &$meta, $logentry, &$hist_ts) {
        $prev = isset($idx['regions'][$id]) ? $idx['regions'][$id] : array();
        $hist = (isset($prev['hist']) && is_array($prev['hist'])) ? $prev['hist'] : array();
        $last = isset($prev['hist_t']) ? intval($prev['hist_t']) : 0;
        $now = time();
        if ($now - $last >= LTU_HIST_INTERVAL) {
            $hist_ts = $now;
            $hist[] = $now;
            while (count($hist) > LTU_HIST_KEEP) array_shift($hist);
            $meta['hist_t'] = $now;
        } else {
            $meta['hist_t'] = $last;
        }
        $meta['hist'] = $hist;
        $idx['regions'][$id] = $meta;
        $idx['log'][] = $logentry;
        while (count($idx['log']) > LTU_LOG_LEN) array_shift($idx['log']);
    });

    if ($hist_ts !== null) {
        // abgespeckter Snapshot fuer das Replay (Position/Hoehe/Kurs)
        $slim = array();
        foreach ($ac as $a) {
            if (!isset($a['lat']) || !isset($a['lon'])) continue;
            $alt = isset($a['alt_baro']) ? $a['alt_baro']
                 : (isset($a['alt_geom']) ? $a['alt_geom'] : null);
            $slim[] = array(
                'hex'    => isset($a['hex']) ? $a['hex'] : null,
                'flight' => isset($a['flight']) ? trim((string)$a['flight']) : null,
                'lat'    => $a['lat'],
                'lon'    => $a['lon'],
                'alt'    => $alt,
                'trk'    => isset($a['track']) ? $a['track'] : null,
            );
            if (count($slim) >= LTU_HIST_MAX_AC) break;
        }
        @file_put_contents(ltu_hist_file($id, $hist_ts),
                           gzencode(json_encode(array('t' => $hist_ts, 'ac' => $slim)), 6),
                           LOCK_EX);
        if (isset($meta['hist'])) ltu_hist_prune($id, $meta['hist']);
    }

    $source_http = isset($_SERVER['HTTP_X_LTU_SOURCE_HTTP'])
                 ? intval($_SERVER['HTTP_X_LTU_SOURCE_HTTP']) : 200;
    if ($source_http < 200 || $source_http >= 300) $source_http = 200;
    $status_rid = isset($_SERVER['HTTP_X_LTU_SOURCE_STATUS'])
                ? (string)$_SERVER['HTTP_X_LTU_SOURCE_STATUS'] : $id;
    $status_id = preg_replace('/[^A-Za-z0-9_\-]/', '', $status_rid);
    if ($status_id === '' || strlen($status_id) > 32) $status_id = $id;
    $source_mode = isset($_SERVER['HTTP_X_LTU_SOURCE_MODE'])
                 ? (string)$_SERVER['HTTP_X_LTU_SOURCE_MODE'] : '';
    if (!in_array($source_mode, array('global', 'regional', 'demand'), true)) {
        $source_mode = (preg_match('/^D[A-F0-9]{12}$/D', $id)) ? 'demand'
                    : (($id === 'global') ? 'global' : 'regional');
    }
    if ($id === 'global' && ($source_name !== 'opensky' || $source_mode !== 'global')) {
        ltu_out(array('error' => 'global snapshots are reserved for OpenSky'), 400);
    }
    ltu_source_status_update($status_id, true, $source_http, $source_mode,
                             $source_name, $source_max_age);
    if ($id === 'global' && $source_name === 'opensky' && $source_mode === 'global') {
        ltu_global_search_complete(time(), LTU_GLOBAL_SEARCH_DEMAND);
    }

    ltu_out(array('ok' => true, 'region' => $id, 'stored' => count($ac),
                  'tracked' => $track_records, 'hist' => ($hist_ts !== null), 't' => time()));
}

// ------------------- COLLECTOR_SOURCE_STATUS (Fetch-Fehler, token-geschuetzt) --
if ($action === 'source_status') {
    if (!ltu_token_ok()) {
        ltu_out(array('error' => 'unauthorized'), 401);
    }
    $rid = (isset($_GET['region']) && is_scalar($_GET['region'])) ? (string)$_GET['region'] : '';
    $id = preg_replace('/[^A-Za-z0-9_\-]/', '', $rid);
    if ($id === '' || strlen($id) > 32) {
        ltu_out(array('error' => 'bad region'), 400);
    }
    $p = json_decode(file_get_contents('php://input'), true);
    if (!is_array($p) || !isset($p['http']) || !is_numeric($p['http'])) {
        ltu_out(array('error' => 'bad payload'), 400);
    }
    $http = intval($p['http']);
    if ($http < 0 || $http > 599) {
        ltu_out(array('error' => 'bad HTTP status'), 400);
    }
    $mode = null;
    if (isset($p['mode'])) {
        if (!is_scalar($p['mode'])
            || !in_array((string)$p['mode'], array('global', 'regional', 'demand'), true)) {
            ltu_out(array('error' => 'bad source mode'), 400);
        }
        $mode = (string)$p['mode'];
    }
    $source = isset($p['source']) && is_scalar($p['source'])
            ? (string)$p['source'] : 'adsb.lol';
    if (!ltu_source_name_is_valid($source)) {
        ltu_out(array('error' => 'bad source'), 400);
    }
    if ($mode === 'global' && $source !== 'opensky') {
        ltu_out(array('error' => 'global mode is reserved for OpenSky'), 400);
    }
    if (strpos($id, 'global-fallback') === 0 || preg_match('/^GF[0-9]{4}/D', $id)) {
        ltu_out(array('error' => 'legacy global-tile fallback is disabled'), 410);
    }
    $max_age = isset($p['max_age']) && is_numeric($p['max_age'])
             ? min(86400, max(0, intval($p['max_age']))) : 0;
    if (!ltu_source_status_update($id, false, $http, $mode, $source, $max_age)) {
        ltu_out(array('error' => 'source status write failed'), 500);
    }
    ltu_out(array('ok' => true, 'region' => $id, 't' => time()));
}

// -------- GLOBAL_SEARCH (Clients request a one-shot global OpenSky snapshot) --
if ($action === 'global_search') {
    $now = time();
    $idx = ltu_load_index();
    $meta = isset($idx['regions']['global']) && is_array($idx['regions']['global'])
          ? $idx['regions']['global'] : null;
    if ($meta !== null && isset($meta['t']) && isset($meta['source'])
        && $meta['source'] === 'opensky'
        && ($now - intval($meta['t'])) <= min(LTU_GLOBAL_SEARCH_CACHE_TTL,
                                               ltu_region_max_age($meta))) {
        $snapshot = ltu_load_region('global');
        if (is_array($snapshot) && isset($snapshot['ac']) && is_array($snapshot['ac'])) {
            ltu_global_search_complete($now, LTU_GLOBAL_SEARCH_DEMAND,
                                       LTU_TOKEN, __DIR__);
            ltu_out(array('ok' => true, 'pending' => false, 'cached' => true,
                          'source' => 'opensky', 't' => intval($meta['t']),
                          'ac' => $snapshot['ac']));
        }
    }

    // Surface a fresh collector-side OpenSky error immediately when no request
    // is active. While a one-shot request is pending, allow the collector's
    // quota/backoff retry to finish before returning a terminal error.
    $source_status = ltu_json_load(LTU_SOURCE_STATUS);
    $opensky_status = isset($source_status['opensky-global'])
                    && is_array($source_status['opensky-global'])
                    ? $source_status['opensky-global'] : null;
    $search_pending = ltu_global_search_is_pending(
        $now, LTU_GLOBAL_SEARCH_DEMAND, LTU_TOKEN, __DIR__, LTU_GLOBAL_SEARCH_TTL);
    if (!$search_pending && $opensky_status !== null && empty($opensky_status['ok'])
        && isset($opensky_status['t'])
        && ($now - intval($opensky_status['t'])) <= ltu_source_max_age($opensky_status)) {
        $http = isset($opensky_status['http']) ? intval($opensky_status['http']) : 0;
        ltu_out(array('ok' => false, 'pending' => false,
                      'rate_limited' => ($http === 429),
                      'retry_after' => ($http === 429) ? LTU_GLOBAL_SEARCH_MIN_INTERVAL : 0,
                      'source' => 'opensky', 'error' => 'OpenSky global is unavailable'),
                 $http === 429 ? 429 : 503);
    }

    $request = ltu_global_search_request(
        $now, LTU_GLOBAL_SEARCH_DEMAND, LTU_TOKEN, __DIR__,
        LTU_GLOBAL_SEARCH_TTL, LTU_GLOBAL_SEARCH_MIN_INTERVAL);
    if (!empty($request['error'])) {
        ltu_out(array('ok' => false, 'pending' => false,
                      'error' => 'global search state unavailable'), 503);
    }
    if (!empty($request['rate_limited'])) {
        ltu_out(array('ok' => false, 'pending' => false, 'rate_limited' => true,
                      'retry_after' => intval($request['retry_after']),
                      'source' => 'opensky', 'error' => 'OpenSky search cooldown'), 429);
    }
    ltu_out(array('ok' => false, 'pending' => !empty($request['pending']),
                  'retry_after' => 3, 'source' => 'opensky',
                  'error' => empty($request['pending'])
                           ? 'OpenSky search request was not queued' : null));
}

// ------------------- CLIENT_DEMAND (Collector, token-geschuetzt) ------------
if ($action === 'demand') {
    if (!ltu_token_ok()) {
        ltu_out(array('error' => 'unauthorized'), 401);
    }
    $now = time();
    $regions = ltu_demand_active_regions($now, LTU_DEMANDS,
                                        LTU_DEMAND_TTL, LTU_DEMAND_MAX_REGIONS);
    $global_search = ltu_global_search_is_pending(
        $now, LTU_GLOBAL_SEARCH_DEMAND, LTU_TOKEN, __DIR__, LTU_GLOBAL_SEARCH_TTL);
    ltu_out(array('ok' => true, 't' => $now, 'active_regions' => count($regions),
                  'global_search' => $global_search, 'regions' => $regions));
}

// ----------------------------- STATUS -----------------------------
if ($action === 'status') {
    $idx = ltu_load_index();
    $now = time();
    $online = 0; $total = 0; $oldest = 0;
    $regions = array();
    $public_demand_index = 0;
    foreach ($idx['regions'] as $id => $r) {
        $age = $now - intval($r['t']);
        $public_id = preg_match('/^D[A-F0-9]{12}$/D', (string)$id)
                   ? 'client-area-' . (++$public_demand_index) : $id;
        $regions[$public_id] = ltu_public_region_meta($id, $r);
        if ($age <= ltu_region_max_age($r)) { $online++; $total += intval($r['count']); }
        $oldest = max($oldest, $age);
    }
    ltu_out(array(
        'online'   => ($online > 0),
        'regions'  => $regions,
        'online_count' => $online,
        'total'    => $total,
        'oldest'   => $oldest,
        'log'      => ltu_public_status_log($idx['log']),
    ));
}

// ----------------------------- HIST (Replay-Snapshots fuer die Konsole) -----
if ($action === 'hist') {
    if (!ltu_token_ok()) {
        ltu_out(array('error' => 'unauthorized'), 401);
    }
    $rid = (isset($_GET['region']) && is_scalar($_GET['region'])) ? (string)$_GET['region'] : 'global';
    $id = preg_replace('/[^A-Za-z0-9_\-]/', '', $rid);
    if ($id === '' || strlen($id) > 32) $id = 'global';
    $idx = ltu_load_index();
    $hist = (isset($idx['regions'][$id]['hist']) && is_array($idx['regions'][$id]['hist']))
          ? $idx['regions'][$id]['hist'] : array();
    $ts = (isset($_GET['ts']) && is_scalar($_GET['ts'])) ? intval($_GET['ts']) : 0;
    if (!$ts && $hist) $ts = intval(end($hist));
    $known = array();
    foreach ($hist as $t) $known[intval($t)] = 1;
    if (!$ts || !isset($known[$ts])) {
        ltu_out(array('error' => 'unknown snapshot', 'region' => $id,
                      'ts_list' => array_keys($known)), 404);
    }
    $snap = ltu_load_hist($id, $ts);
    if (!$snap) {
        ltu_out(array('error' => 'snapshot file missing', 'region' => $id, 'ts' => $ts), 404);
    }
    $snap['region'] = $id;
    ltu_out($snap);
}

// ------------------- TRACK (Letzte Stunde pro ICAO-Hex, offen) ----------------
if ($action === 'track') {
    $hex = (isset($_GET['hex']) && is_scalar($_GET['hex']))
         ? strtolower(trim((string)$_GET['hex'])) : '';
    $flight = (isset($_GET['flight']) && is_scalar($_GET['flight']))
            ? strtoupper(trim((string)$_GET['flight'])) : '';
    if ($hex !== '' && preg_match('/^[a-f0-9]{1,16}$/D', $hex)) {
        $track = ltu_track_load($hex, time());
    } elseif ($flight !== '' && preg_match('/^[A-Z0-9-]{1,16}$/D', $flight)) {
        $track = ltu_track_load_flight($flight, time());
    } else {
        ltu_out(array('ok' => false, 'error' => 'bad aircraft hex or callsign'), 400);
    }
    ltu_track_prune_expired(time());
    $points = $track && isset($track['points']) ? $track['points'] : array();
    ltu_out(array(
        'ok' => true,
        'found' => count($points) > 0,
        'hex' => $track ? $track['hex'] : $hex,
        'flight' => $track ? $track['flight'] : $flight,
        'updated' => $track ? $track['updated'] : 0,
        'window_sec' => LTU_TRACK_RETENTION_SEC,
        'points' => $points,
    ));
}

// ------------------- LOGOUT (Client meldet sich sofort ab) ------------------
if ($action === 'logout') {
    if (!isset($_SERVER['REQUEST_METHOD']) || $_SERVER['REQUEST_METHOD'] !== 'POST') {
        ltu_out(array('error' => 'method not allowed'), 405);
    }
    $p = json_decode(file_get_contents('php://input'), true);
    if (!is_array($p) || !isset($p['uid']) || !is_scalar($p['uid'])) {
        ltu_out(array('error' => 'bad payload'), 400);
    }
    $uid = preg_replace('/[^A-Za-z0-9_\-]/', '', (string)$p['uid']);
    if ($uid === '' || strlen($uid) > 32) {
        ltu_out(array('error' => 'bad uid'), 400);
    }
    $removed = false;
    if (!ltu_clients_update(function (&$cl) use ($uid, &$removed) {
        if (isset($cl[$uid])) {
            unset($cl[$uid]);
            $removed = true;
        }
    })) {
        ltu_out(array('error' => 'client store unavailable'), 500);
    }
    if ($removed) {
        ltu_json_update(LTU_MESH_F, function (&$mesh) use ($uid) {
            if (isset($mesh['ersatz_uid']) && (string)$mesh['ersatz_uid'] === $uid) {
                $mesh['ersatz_uid'] = '';
                $mesh['elected_at'] = 0;
            }
            if (isset($mesh['yields'][$uid])) unset($mesh['yields'][$uid]);
        });
    }
    ltu_out(array('ok' => true, 'removed' => $removed, 'uid' => $uid, 't' => time()));
}

// ------------------- HEARTBEAT (Client-Status, offen, rate-limited) --------
if ($action === 'heartbeat') {
    $raw = file_get_contents('php://input');
    $hb = json_decode($raw, true);
    if (!is_array($hb) || !isset($hb['uid']) || !is_scalar($hb['uid'])) {
        ltu_out(array('error' => 'bad payload'), 400);
    }
    $uid = preg_replace('/[^A-Za-z0-9_\-]/', '', (string)$hb['uid']);
    if ($uid === '' || strlen($uid) > 32) {
        ltu_out(array('error' => 'bad uid'), 400);
    }
    $role = (isset($hb['role']) && is_scalar($hb['role'])
             && in_array($hb['role'], array('client', 'candidate', 'ersatz'), true))
          ? $hb['role'] : 'client';
    $st = (isset($hb['statuses']) && is_array($hb['statuses']))
        ? array_slice($hb['statuses'], 0, 12, true) : array();
    $entry = array(
        't'        => time(),
        'name'     => (isset($hb['name']) && is_scalar($hb['name']))
                    ? substr((string)$hb['name'], 0, 40) : $uid,
        'role'     => $role,
        'proxy_ok' => !empty($hb['proxy_ok']),
        'net_ok'   => !empty($hb['net_ok']),
        'peers'    => (isset($hb['peers']) && is_scalar($hb['peers'])) ? intval($hb['peers']) : 0,
        'ip'       => isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : '?',
        'statuses' => $st,
    );
    $accepted = false;
    ltu_clients_update(function (&$cl) use ($uid, $entry, &$accepted) {
        if (isset($cl[$uid]) && is_array($cl[$uid]) && (time() - intval($cl[$uid]['t'])) < 2) {
            return;                                   // Spam-Schutz: 1 Heartbeat/2s
        }
        $cl[$uid] = $entry;
        $accepted = true;
        foreach ($cl as $k => $v) {
            if (!is_array($v) || (time() - intval($v['t'])) > LTU_CLIENT_TTL) unset($cl[$k]);
        }
        if (count($cl) > 200) {
            uasort($cl, function ($a, $b) { return intval($b['t']) - intval($a['t']); });
            $cl = array_slice($cl, 0, 200, true);
        }
    });
    // Serverseitige Mesh-Entscheidung: Rolle fuer diesen Client + Wahl-Zustand.
    $now = time();
    $m_idx = ltu_load_index();
    $collector_online = ltu_collector_online($m_idx, $now);
    $cl2 = ltu_load_clients();
    $resp = array('mesh_active' => false, 'ersatz_uid' => '', 'ersatz' => null,
                  'hb' => LTU_HB_NORMAL, 'clients' => 0);
    ltu_json_update(LTU_MESH_F, function (&$m) use ($cl2, $collector_online, $now, &$resp) {
        list($active, $ers_uid, $ers_name, $hb) =
            ltu_mesh_decide($cl2, $m, $collector_online, $now);
        $n = 0;
        foreach ($cl2 as $c) {
            if (is_array($c) && isset($c['t'])
                && ($now - intval($c['t'])) <= LTU_CLIENT_FRESH) $n++;
        }
        $resp = array('mesh_active' => $active, 'ersatz_uid' => $ers_uid,
                      'ersatz' => $ers_name, 'hb' => $hb, 'clients' => $n);
    });
    $my_role = ($resp['ersatz_uid'] !== '' && $resp['ersatz_uid'] === $uid) ? 'ersatz' : 'client';
    ltu_out(array(
        'ok'          => $accepted,
        't'           => $now,
        'role'        => $my_role,
        'mesh_active' => $resp['mesh_active'],
        'ersatz'      => ($my_role === 'ersatz') ? null : $resp['ersatz'],
        'ersatz_uid'  => ($my_role === 'ersatz') ? $uid : $resp['ersatz_uid'],
        'hb'          => $resp['hb'],
        'clients'     => $resp['clients'],
    ));
}

// ------------- RELAY_PUSH (nur der gewaehlte Ersatz-Proxy, ohne Token) ------
if ($action === 'relay_push') {
    $raw = file_get_contents('php://input');
    $p = json_decode($raw, true);
    if (!is_array($p) || !isset($p['uid']) || !is_scalar($p['uid'])
        || !isset($p['ac']) || !is_array($p['ac'])) {
        ltu_out(array('ok' => false, 'error' => 'bad payload'), 400);
    }
    $uid = preg_replace('/[^A-Za-z0-9_\-]/', '', (string)$p['uid']);
    $mesh = array_merge(array('ersatz_uid' => ''), ltu_json_load(LTU_MESH_F));
    if ($uid === '' || (string)$mesh['ersatz_uid'] !== $uid) {
        ltu_out(array('ok' => false, 'error' => 'not-ersatz'));
    }
    $bbox = array(90.0, -90.0, 180.0, -180.0);
    if (isset($p['bbox']) && is_array($p['bbox']) && count($p['bbox']) === 4) {
        $bbox = array_map('floatval', $p['bbox']);
    }
    $ac = array_slice($p['ac'], 0, LTU_MAX_AC);
    $track_records = ltu_track_record_batch($ac, time());
    ltu_track_prune_expired(time());
    $rname = (isset($p['name']) && is_scalar($p['name']))
           ? substr((string)$p['name'], 0, 40) : $uid;
    $nowf = microtime(true);
    ltu_json_update(LTU_RELAY, function (&$r) use ($uid, $rname, $bbox, $ac, $nowf) {
        if (is_array($r) && isset($r['ts']) && isset($r['uid']) && $r['uid'] === $uid
            && ($nowf - floatval($r['ts'])) < 2.0) {
            return;                                   // Spam-Schutz: 1 Push/2s
        }
        $r = array('ts' => $nowf, 'uid' => $uid, 'name' => $rname,
                   'bbox' => $bbox, 'ac' => $ac, 'count' => count($ac));
    });
    ltu_out(array('ok' => true, 'count' => count($ac),
                  'tracked' => $track_records, 't' => $nowf));
}

// ------------- RELAY_PULL (Clients holen Traffic vom Web-Relay, offen) ------
if ($action === 'relay_pull') {
    $rl = ltu_json_load(LTU_RELAY);
    $nowf = microtime(true);
    if (!$rl || !isset($rl['ts']) || ($nowf - floatval($rl['ts'])) > LTU_RELAY_MAX_AGE) {
        ltu_out(array('ok' => false, 'error' => 'stale'));
    }
    $query_bbox = null;
    $circle = null;
    if (isset($_GET['bbox']) && is_scalar($_GET['bbox'])) {
        $pp = array_map('floatval', explode(',', (string)$_GET['bbox']));
        if (count($pp) === 4) $query_bbox = array($pp[0], $pp[1], $pp[2], $pp[3]); // n,s,e,w
    }
    if ($query_bbox === null && isset($_GET['lat']) && isset($_GET['lon'])
        && isset($_GET['dist']) && is_scalar($_GET['lat']) && is_scalar($_GET['lon'])
        && is_scalar($_GET['dist'])) {
        $circle = array(ltu_num('lat'), ltu_num('lon'), ltu_num('dist'));
        $query_bbox = ltu_bbox_from_circle($circle[0], $circle[1], max(1.0, $circle[2]));
    }
    if ($query_bbox === null) $query_bbox = array(90.0, -90.0, 180.0, -180.0);
    $ac = array();
    foreach ((array)$rl['ac'] as $a) {
        if (!is_array($a) || !isset($a['lat']) || !isset($a['lon'])
            || !is_numeric($a['lat']) || !is_numeric($a['lon'])) continue;
        $la = floatval($a['lat']);
        $lo = floatval($a['lon']);
        if ($la < $query_bbox[1] || $la > $query_bbox[0]
            || !ltu_lon_in_bbox($lo, $query_bbox)) continue;
        if ($circle !== null
            && ltu_haversine_km($circle[0], $circle[1], $la, $lo) > $circle[2]) continue;
        $ac[] = $a;
    }
    ltu_track_record_batch($ac, time());
    ltu_track_prune_expired(time());
    ltu_out(array('ok' => true, 'ac' => $ac, 't' => $rl['ts'], 'count' => count($ac),
                  'age' => round($nowf - floatval($rl['ts']), 1),
                  'src' => 'ersatz-relay',
                  'ersatz' => isset($rl['name']) ? $rl['name'] : null));
}

// ------------------- HEALTHDATA (JSON fuer die Statusseite, offen) ----------
if ($action === 'healthdata') {
    $idx = ltu_load_index();
    $cl = ltu_load_clients();
    $now = time();
    $online = 0; $total = 0; $oldest = 0;
    $public_regions = array();
    $public_demand_index = 0;
    foreach ($idx['regions'] as $id => $r) {
        $age = $now - intval($r['t']);
        $public_id = preg_match('/^D[A-F0-9]{12}$/D', (string)$id)
                   ? 'client-area-' . (++$public_demand_index) : $id;
        $public_regions[$public_id] = ltu_public_region_meta($id, $r);
        if ($age <= ltu_region_max_age($r)) { $online++; $total += intval($r['count']); }
        $oldest = max($oldest, $age);
    }
    $source_status = ltu_json_load(LTU_SOURCE_STATUS);
    $fresh = array();
    foreach ($cl as $uid => $c) {
        if (is_array($c) && ($now - intval($c['t'])) <= 90) $fresh[$uid] = $c;
    }
    $mesh = array_merge(array('ersatz_uid' => '', 'elected_at' => 0),
                        ltu_json_load(LTU_MESH_F));
    $ers_uid = (string)$mesh['ersatz_uid'];
    $ersatz = null;
    $mesh_active = false;
    if ($ers_uid !== '' && isset($fresh[$ers_uid])
        && ($now - intval($fresh[$ers_uid]['t'])) <= LTU_ERSATZ_STALE) {
        $ersatz = $fresh[$ers_uid]['name'];
        $mesh_active = true;
    }
    foreach ($fresh as $u => $c) {   // Rollen = Wahrheit des Servers
        $fresh[$u]['role'] = ($mesh_active && $u === $ers_uid) ? 'ersatz' : 'client';
    }
    $rl = ltu_json_load(LTU_RELAY);
    $relay_age = isset($rl['ts']) ? round(microtime(true) - floatval($rl['ts']), 1) : null;
    $active_demands = ltu_demand_active_regions($now, LTU_DEMANDS,
                                                 LTU_DEMAND_TTL, LTU_DEMAND_MAX_REGIONS);
    $global_search_pending = ltu_global_search_is_pending(
        $now, LTU_GLOBAL_SEARCH_DEMAND, LTU_TOKEN, __DIR__, LTU_GLOBAL_SEARCH_TTL);
    $demand_paused = count($active_demands) === 0 && !$global_search_pending;
    ltu_out(array(
        'now'      => $now,
        'max_age'  => LTU_MAX_AGE,
        'demand'   => array('active_regions' => count($active_demands),
                            'global_search_pending' => $global_search_pending,
                            'paused' => $demand_paused,
                            'ttl' => LTU_DEMAND_TTL),
        'proxy'    => array(
            'reachable' => true,   // diese Antwort kommt vom Proxy selbst
            'collector_online' => ($online > 0),
            'regions_online' => $online,
            'regions_total' => count($public_regions),
            'total_ac'   => $total,
            'oldest'     => $oldest,
        ),
        'regions'  => $public_regions,
        'clients'  => $fresh,
        'source_status' => ltu_public_source_status($source_status),
        'source_max_age' => LTU_SOURCE_FRESH,
        'ersatz'   => $ersatz,
        'mesh'     => array(
            'active'     => $mesh_active,
            'uid'        => $mesh_active ? $ers_uid : '',
            'elected_at' => intval($mesh['elected_at']),
            'relay_age'  => $relay_age,
            'relay_count' => isset($rl['count']) ? intval($rl['count']) : 0,
        ),
    ));
}

// ------------------- STATUSPAGE (oeffentliche Statusanzeige) ----------------
if ($action === 'statuspage') {
    ltu_html(ltu_statuspage_html());
}

// ----------------------------- TRAFFIC (Clients, Bereichs-Filter) -----------------------------
// Jeder echte Traffic-Read erneuert nur einen grob gerasterten Bereich. Der
// Collector fragt diese Bereiche token-geschuetzt ab und pausiert nach Ablauf.
$demand_recorded = false;
if ($action === 'traffic') {
    $demand_recorded = ltu_record_client_demand(
        $_GET, time(), LTU_DEMANDS, LTU_TOKEN,
        LTU_DEMAND_TTL, LTU_DEMAND_WRITE_INTERVAL, LTU_DEMAND_MAX_REGIONS
    );
}
$idx = ltu_load_index();
if (!$idx['regions']) {
    ltu_out(array('now' => microtime(true), 'ac' => array(),
                  'demand_recorded' => $demand_recorded,
                  'proxy' => array('online' => false, 'reason' => 'collector hat noch nicht gepusht')));
}

// Query-Bereich bestimmen: bbox bevorzugt, sonst Kreis, sonst Welt
$query_bbox = null;
$circle = null;
if (isset($_GET['bbox']) && is_scalar($_GET['bbox'])) {
    $p = array_map('floatval', explode(',', (string)$_GET['bbox']));
    if (count($p) === 4) {
        $query_bbox = array($p[0], $p[1], $p[2], $p[3]); // n,s,e,w
    }
}
if ($query_bbox === null && isset($_GET['lat']) && isset($_GET['lon']) && isset($_GET['dist'])
    && is_scalar($_GET['lat']) && is_scalar($_GET['lon']) && is_scalar($_GET['dist'])) {
    $circle = array(ltu_num('lat'), ltu_num('lon'), ltu_num('dist'));
    $query_bbox = ltu_bbox_from_circle($circle[0], $circle[1], max(1.0, $circle[2]));
}
if ($query_bbox === null) {
    $query_bbox = array(90.0, -90.0, 180.0, -180.0);
}

// Nur Regionen entspacken, die den Query-Bereich schneiden
$cands = array();
foreach ($idx['regions'] as $id => $r) {
    if (isset($r['bbox']) && ltu_bbox_intersects($r['bbox'], $query_bbox)) {
        $cands[$id] = $r;
    }
}
uasort($cands, function ($a, $b) { return $b['t'] - $a['t']; }); // neueste zuerst (Dedup)

$merged = array();
$track_candidates = array();
$seen = array();
$fresh = false;
$now = time();
foreach ($cands as $id => $r) {
    $reg = ltu_load_region($id);
    if (!$reg || !isset($reg['ac'])) continue;
    $region_age = $now - intval($r['t']);
    $region_max_age = ltu_region_max_age($r);
    $region_is_fresh = $region_age <= $region_max_age;
    if ($region_is_fresh) {
        $fresh = true;
        foreach ($reg['ac'] as $track_aircraft) $track_candidates[] = $track_aircraft;
    }
    foreach ($reg['ac'] as $a) {
        $hex = isset($a['hex']) ? $a['hex'] : null;
        if ($hex !== null) {
            if (isset($seen[$hex])) continue;
            $seen[$hex] = 1;
        }
        $merged[] = $a;
    }
}

// Exakter Filter innerhalb des Bereichs
$ac = array();
foreach ($merged as $a) {
    if (!isset($a['lat']) || !isset($a['lon']) ||
        !is_numeric($a['lat']) || !is_numeric($a['lon'])) continue;
    $la = floatval($a['lat']); $lo = floatval($a['lon']);
    if ($la < $query_bbox[1] || $la > $query_bbox[0] ||
        !ltu_lon_in_bbox($lo, $query_bbox)) continue;
    if ($circle !== null &&
        ltu_haversine_km($circle[0], $circle[1], $la, $lo) > $circle[2]) continue;
    $ac[] = $a;
}

ltu_track_record_batch($track_candidates, $now);
ltu_track_prune_expired($now);
ltu_out(array(
    'now' => microtime(true),
    'ac' => $ac,
    'demand_recorded' => $demand_recorded,
    'proxy' => array(
        'online'  => $fresh,
        'regions' => count($cands),
        'count'   => count($ac),
    ),
));
