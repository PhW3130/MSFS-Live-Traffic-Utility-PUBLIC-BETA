<?php
require_once __DIR__ . '/../proxy/client_demand.php';

$errors = array();
function check_demand($condition, $message) {
    global $errors;
    if ($condition) echo "  [OK] $message\n";
    else { echo "  [FAIL] $message\n"; $errors[] = $message; }
}

$secret = 'unit-test-secret-not-for-production';
$private_path = ltu_demand_store_path($secret, __DIR__);
check_demand(dirname($private_path) === rtrim(sys_get_temp_dir(), DIRECTORY_SEPARATOR)
    && strpos($private_path, __DIR__) === false,
    'Nachfrage-Cache liegt ausserhalb des Webroots');
check_demand(ltu_source_name_is_valid('planetrack')
    && ltu_source_name_is_valid('opensky')
    && !ltu_source_name_is_valid('unknown'),
    'Collector akzeptiert PlaneTrack als Quelle, aber keine unbekannten Namen');
$circle = ltu_demand_area_from_query(array('lat' => '50.03', 'lon' => '8.57', 'dist' => '250'));
check_demand($circle !== null && abs($circle['lat'] - 50.03) < 0.001
    && abs($circle['lon'] - 8.57) < 0.001 && $circle['radius_km'] === 250,
    'Kreis-Anfrage wird als begrenzte Region erkannt');

$bbox = ltu_demand_area_from_query(array('lat' => '50', 'lon' => '8', 'dist' => '250',
                                         'bbox' => '51,49,10,6'));
check_demand($bbox !== null && abs($bbox['lat'] - 50.0) < 0.001
    && abs($bbox['lon'] - 8.0) < 0.001 && $bbox['radius_km'] > 100,
    'BBox wird durch einen Kreis abgedeckt');

$date_line = ltu_demand_area_from_query(array('bbox' => '1,-1,-179,179'));
check_demand($date_line !== null && abs(abs($date_line['lon']) - 180.0) < 0.01,
    'BBox am Datumswechsel wird korrekt umgebrochen');
check_demand(ltu_demand_area_from_query(array('lat' => '91', 'lon' => '0', 'dist' => '5')) === null,
    'Ungueltige Koordinaten erzeugen keine Nachfrage');
check_demand(ltu_demand_area_from_query(array('lat' => '50', 'lon' => '8', 'dist' => '-1')) === null,
    'Ungueltiger Radius erzeugt keine Nachfrage');

$zone1 = ltu_demand_region_for_area($circle, $secret);
$nearby = ltu_demand_area_from_query(array('lat' => '50.04', 'lon' => '8.56', 'dist' => '300'));
$zone2 = ltu_demand_region_for_area($nearby, $secret);
check_demand($zone1 !== null && preg_match('/^D[A-F0-9]{12}$/D', $zone1['id']) === 1
    && $zone1['id'] === $zone2['id'],
    'Nahe Client-Anfragen teilen eine undurchsichtige Raster-ID');
check_demand($zone1['radius_km'] >= $circle['radius_km']
    && $zone1['radius_km'] <= 463,
    'Rasterregion deckt die Client-Anfrage ab und bleibt innerhalb des Feed-Limits');

$corrupt_file = tempnam(sys_get_temp_dir(), 'ltu-demand-corrupt-');
file_put_contents($corrupt_file, '{broken json');
$corrupt_write = ltu_demand_store_region($zone1, 999, $corrupt_file, 20, 5, 128);
$repaired = json_decode(file_get_contents($corrupt_file), true);
check_demand($corrupt_write && is_array($repaired)
    && isset($repaired[$zone1['id']]),
    'Beschaedigter Demand-Store wird gemeldet und mit frischer Nachfrage repariert');
@unlink($corrupt_file);

$missing_dir = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'ltu-demand-missing-' . uniqid('', true);
$missing_file_write = ltu_demand_store_region(
    $zone1, 999, $missing_dir . DIRECTORY_SEPARATOR . 'demand.json', 20, 5, 128
);
check_demand(!$missing_file_write,
    'Nicht beschreibbarer Demand-Pfad wird als Fehler zurueckgegeben');

$file = tempnam(sys_get_temp_dir(), 'ltu-demand-');
@unlink($file);
$stored = ltu_demand_store_region($zone1, 1000, $file, 20, 5, 128);
$stored_nearby = ltu_demand_store_region($zone2, 1001, $file, 20, 5, 128);
$active_now = ltu_demand_active_regions(1001, $file, 20, 128);
check_demand($stored && $stored_nearby && count($active_now) === 1
    && $active_now[0]['id'] === $zone1['id']
    && $active_now[0]['radius_km'] >= $zone2['radius_km'],
    'Aktive Nachfrage wird zusammengefasst und deckt den groesseren Client-Bereich ab');

$still_active = ltu_demand_active_regions(1020, $file, 20, 128);
$expired = ltu_demand_active_regions(1022, $file, 20, 128);
check_demand(count($still_active) === 1 && count($expired) === 0,
    'Nachfrage laeuft nach Ablauf des TTL automatisch ab');

$private = ltu_public_region_meta($zone1['id'], array(
    'demand_driven' => true, 't' => 1000, 'count' => 5, 'center' => array(50.125, 8.125),
    'bbox' => array(55, 45, 15, 0), 'radius' => 463, 'source' => 'adsb.lol'
));
check_demand(isset($private['demand_driven']) && !isset($private['center'])
    && !isset($private['bbox']) && !isset($private['radius']),
    'Oeffentliche Status-Metadaten geben keine Client-Koordinaten preis');
$private_bad_source = ltu_public_region_meta($zone1['id'], array(
    'demand_driven' => true, 'source' => 'must-not-be-public', 't' => 1004
));
check_demand(!isset($private_bad_source['source'])
    && strpos(json_encode($private_bad_source), 'must-not-be-public') === false,
    'Collector-Quellnamen sind auch in oeffentlichen Regions-Metadaten begrenzt');

$public_sources = ltu_public_source_status(array(
    $zone1['id'] => array('source' => 'adsb.lol', 'mode' => 'demand', 't' => 1000),
    $zone1['id'] . '-adsb-fi' => array('source' => 'adsb.fi', 'mode' => 'demand', 't' => 1001),
    $zone1['id'] . '-planetrack' => array(
        'source' => 'planetrack', 'mode' => 'demand', 'ok' => false,
        'http' => 401, 't' => 1004, 'api_key' => 'must-not-be-public',
        'detail' => 'must-not-be-public either'
    ),
    'must-not-be-public' => array('source' => 'planetrack', 'ok' => false, 't' => 1005),
    'global' => array('source' => 'adsb.lol', 'mode' => 'global', 't' => 1002),
    'global-fallback-GF0001' => array('source' => 'adsb.lol',
                                     'mode' => 'global-fallback', 't' => 1003),
));
$public_log = ltu_public_status_log(array(array('region' => $zone1['id'], 'count' => 5)));
check_demand(isset($public_sources['client-demand-adsb.lol'])
    && isset($public_sources['client-demand-adsb.fi'])
    && isset($public_sources['client-demand-planetrack'])
    && isset($public_sources['collector-source'])
    && $public_sources['client-demand-planetrack']['source'] === 'planetrack'
    && !isset($public_sources['client-demand-planetrack']['api_key'])
    && !isset($public_sources['client-demand-planetrack']['detail'])
    && strpos(json_encode($public_sources), 'must-not-be-public') === false
    && !isset($public_sources[$zone1['id']])
    && !isset($public_sources['global'])
    && !isset($public_sources['global-fallback-GF0001'])
    && $public_log[0]['region'] === 'client-demand',
    'Public source status adds anonymized PlaneTrack status and scrubs secret fields');

$global_file = tempnam(sys_get_temp_dir(), 'ltu-global-search-');
@unlink($global_file);
$global_path = ltu_global_search_store_path($secret, __DIR__);
check_demand(dirname($global_path) === rtrim(sys_get_temp_dir(), DIRECTORY_SEPARATOR)
    && strpos($global_path, __DIR__) === false,
    'Globaler Such-Request wird nur als private Zeitstempel ausserhalb des Webroots gespeichert');
$global_request = ltu_global_search_request(1000, $global_file, $secret, __DIR__, 180, 120);
$coalesced_request = ltu_global_search_request(1001, $global_file, $secret, __DIR__, 180, 120);
check_demand(!empty($global_request['pending']) && !empty($coalesced_request['pending'])
    && ltu_global_search_is_pending(1002, $global_file, $secret, __DIR__, 180),
    'Aktive globale Suchanfragen werden gesammelt und parallel coalesziert');
$global_limited = ltu_global_search_request(1005, $global_file, $secret, __DIR__, 180, 120);
check_demand(!empty($global_limited['pending']) && empty($global_limited['rate_limited']),
    'Ein noch laufender globaler Request wird nicht als Duplikat rate-limitiert');
check_demand(ltu_global_search_complete(1006, $global_file, $secret, __DIR__)
    && !ltu_global_search_is_pending(1007, $global_file, $secret, __DIR__, 180),
    'Collector-Push beendet die aktive globale Suche');
$cooldown = ltu_global_search_request(1008, $global_file, $secret, __DIR__, 180, 120);
check_demand(empty($cooldown['pending']) && !empty($cooldown['rate_limited'])
    && $cooldown['retry_after'] === 112,
    'Neue globale Abfrage wird zentral mindestens 120 Sekunden begrenzt');
$after_cooldown = ltu_global_search_request(1120, $global_file, $secret, __DIR__, 180, 120);
check_demand(!empty($after_cooldown['pending']),
    'Neuer globaler Request wird nach Ablauf des Cooldowns zugelassen');
@unlink($global_file);

@unlink($file);
if ($errors) exit(1);
echo "Client-demand PHP checks passed\n";
