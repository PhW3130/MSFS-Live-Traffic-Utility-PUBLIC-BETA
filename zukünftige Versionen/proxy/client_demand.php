<?php
/**
 * Kurzlebige, grob gerasterte Client-Nachfrage fuer den Collector.
 *
 * Gespeichert werden weder IP-Adressen noch Client-IDs. Anfragen innerhalb
 * derselben 0,25-Grad-Zelle werden zusammengefasst; der Collector kann die
 * Bereiche nur mit dem PHP-Token abrufen. Ein Bereich laeuft nach kurzer Zeit
 * ohne neue Traffic-Abfrage automatisch ab.
 */

function ltu_demand_store_path($secret = null, $scope = null) {
    if ($secret === null) $secret = defined('LTU_TOKEN') ? LTU_TOKEN : '';
    if ($scope === null) $scope = __DIR__;
    $directory = rtrim(sys_get_temp_dir(), DIRECTORY_SEPARATOR);
    return $directory . DIRECTORY_SEPARATOR . 'ltu-demand-'
         . substr(hash('sha256', (string)$scope . "\0" . (string)$secret), 0, 24) . '.json';
}

function ltu_demand_wrap_lon($lon) {
    $wrapped = fmod(floatval($lon) + 180.0, 360.0);
    if ($wrapped < 0) $wrapped += 360.0;
    return $wrapped - 180.0;
}

function ltu_demand_haversine_km($lat1, $lon1, $lat2, $lon2) {
    $earth = 6371.0;
    $p1 = deg2rad(floatval($lat1));
    $p2 = deg2rad(floatval($lat2));
    $dp = deg2rad(floatval($lat2) - floatval($lat1));
    $dl = deg2rad(ltu_demand_wrap_lon(floatval($lon2) - floatval($lon1)));
    $a = sin($dp / 2.0) * sin($dp / 2.0)
       + cos($p1) * cos($p2) * sin($dl / 2.0) * sin($dl / 2.0);
    return 2.0 * $earth * asin(min(1.0, sqrt(max(0.0, $a))));
}

/** Liefert den angefragten Kreis bzw. die den BBox abdeckende Kreisregion. */
function ltu_demand_area_from_query($query) {
    if (!is_array($query)) return null;

    if (isset($query['bbox']) && is_scalar($query['bbox'])) {
        $parts = explode(',', (string)$query['bbox']);
        if (count($parts) === 4) {
            $valid = true;
            foreach ($parts as $part) {
                if (!is_numeric($part) || !is_finite(floatval($part))) $valid = false;
            }
            if ($valid) {
                // Query-Format: north,south,east,west. Leaflet kann am
                // Datumswechsel Werte ausserhalb +/-180 liefern, daher nur
                // auf eine plausible Groesse begrenzen und spaeter wrappen.
                $north = floatval($parts[0]);
                $south = floatval($parts[1]);
                $east = floatval($parts[2]);
                $west = floatval($parts[3]);
                if ($north >= $south && $north <= 90.0 && $south >= -90.0
                    && abs($east) <= 540.0 && abs($west) <= 540.0) {
                    $width = $east - $west;
                    while ($width < 0.0) $width += 360.0;
                    $width = min(360.0, $width);
                    $lat = ($north + $south) / 2.0;
                    $lon = ltu_demand_wrap_lon($west + $width / 2.0);
                    $radius = 1.0;
                    foreach (array($north, $south) as $corner_lat) {
                        foreach (array($west, $east) as $corner_lon) {
                            $radius = max($radius, ltu_demand_haversine_km(
                                $lat, $lon, $corner_lat, $corner_lon));
                        }
                    }
                    return array(
                        'lat' => $lat,
                        'lon' => $lon,
                        'radius_km' => min(463, max(1, intval(ceil($radius)))),
                    );
                }
            }
        }
    }

    if (!isset($query['lat'], $query['lon'])
        || !is_scalar($query['lat']) || !is_scalar($query['lon'])
        || !is_numeric($query['lat']) || !is_numeric($query['lon'])) return null;
    $lat = floatval($query['lat']);
    $lon = floatval($query['lon']);
    if (!is_finite($lat) || !is_finite($lon)
        || $lat < -90.0 || $lat > 90.0 || $lon < -180.0 || $lon > 180.0) return null;
    $radius = (isset($query['dist']) && is_scalar($query['dist'])
               && is_numeric($query['dist'])) ? floatval($query['dist']) : 250.0;
    if (!is_finite($radius) || $radius <= 0.0) return null;
    return array(
        'lat' => $lat,
        'lon' => ltu_demand_wrap_lon($lon),
        'radius_km' => min(463, max(1, intval(ceil($radius)))),
    );
}

/**
 * Fasst Nachfrage in 0,25-Grad-Zellen zusammen und gibt eine opaque ID aus.
 * Der Zellmittelpunkt plus angefragter Radius deckt den originalen Bereich ab.
 */
function ltu_demand_region_for_area($area, $secret) {
    if (!is_array($area) || !isset($area['lat'], $area['lon'], $area['radius_km'])
        || !is_numeric($area['lat']) || !is_numeric($area['lon'])
        || !is_numeric($area['radius_km'])) return null;
    $lat = floatval($area['lat']);
    $lon = floatval($area['lon']);
    $radius = intval($area['radius_km']);
    if (!is_finite($lat) || !is_finite($lon) || $lat < -90.0 || $lat > 90.0
        || $lon < -180.0 || $lon > 180.0 || $radius < 1 || $radius > 463) return null;

    $grid = 0.25;
    $lat_cell = max(0, min(719, intval(floor(($lat + 90.0) / $grid))));
    $lon = ltu_demand_wrap_lon($lon);
    $lon_cell = max(0, min(1439, intval(floor(($lon + 180.0) / $grid))));
    $cell_lat = min(90.0, -90.0 + ($lat_cell + 0.5) * $grid);
    $cell_lon = ltu_demand_wrap_lon(-180.0 + ($lon_cell + 0.5) * $grid);
    $offset = ltu_demand_haversine_km($lat, $lon, $cell_lat, $cell_lon);
    $cover_radius = min(463, max(1, intval(ceil($radius + $offset))));
    $cell_key = $lat_cell . ':' . $lon_cell;
    $id = 'D' . strtoupper(substr(hash_hmac('sha256', $cell_key, (string)$secret), 0, 12));

    return array('id' => $id, 'lat' => $cell_lat, 'lon' => $cell_lon,
                 'radius_km' => $cover_radius);
}

/** File-Locked update; returns false if the demand store cannot be written. */
function ltu_demand_store_region($region, $now = null, $file = null,
                                 $ttl = 20, $write_interval = 5,
                                 $max_regions = 128) {
    if (!is_array($region) || !isset($region['id'], $region['lat'], $region['lon'], $region['radius_km'])
        || !preg_match('/^D[A-F0-9]{12}$/D', (string)$region['id'])) return false;
    if ($now === null) $now = time();
    if ($file === null) $file = ltu_demand_store_path();
    $lat = floatval($region['lat']);
    $lon = floatval($region['lon']);
    $radius = intval($region['radius_km']);
    if (!is_finite($lat) || !is_finite($lon) || $lat < -90.0 || $lat > 90.0
        || $lon < -180.0 || $lon > 180.0 || $radius < 1 || $radius > 463) return false;

    $fp = @fopen($file, 'c+b');
    if (!$fp) return false;
    @chmod($file, 0600);
    if (!flock($fp, LOCK_EX)) {
        fclose($fp);
        return false;
    }
    $raw = stream_get_contents($fp);
    if ($raw === false) {
        flock($fp, LOCK_UN);
        fclose($fp);
        return false;
    }
    $changed = false;
    if ($raw === '') {
        $store = array();
    } else {
        $store = json_decode($raw, true);
        if (!is_array($store) || json_last_error() !== JSON_ERROR_NONE) {
            error_log('[LTU] Resetting an invalid client-demand cache before writing fresh demand.');
            $store = array();
            $changed = true;
        }
    }
    foreach ($store as $id => $entry) {
        if (!is_array($entry) || !isset($entry['t'])
            || intval($entry['t']) > $now || ($now - intval($entry['t'])) > $ttl) {
            unset($store[$id]);
            $changed = true;
        }
    }

    $id = (string)$region['id'];
    $previous = isset($store[$id]) && is_array($store[$id]) ? $store[$id] : null;
    if ($previous === null) {
        $store[$id] = array('id' => $id, 't' => intval($now), 'lat' => $lat,
                            'lon' => $lon, 'radius_km' => $radius);
        $changed = true;
    } else {
        $radius = max($radius, intval($previous['radius_km']));
        if (($now - intval($previous['t'])) >= $write_interval
            || $radius > intval($previous['radius_km'])) {
            $store[$id] = array('id' => $id, 't' => intval($now), 'lat' => $lat,
                                'lon' => $lon, 'radius_km' => $radius);
            $changed = true;
        }
    }

    if (count($store) > $max_regions) {
        uasort($store, function ($a, $b) { return intval($b['t']) - intval($a['t']); });
        $store = array_slice($store, 0, $max_regions, true);
        $changed = true;
    }
    if ($changed) {
        $encoded = json_encode($store);
        $written = false;
        if ($encoded !== false && ftruncate($fp, 0) && rewind($fp)) {
            $bytes = fwrite($fp, $encoded);
            $written = ($bytes === strlen($encoded)) && fflush($fp);
        }
    } else {
        $written = true;
    }
    flock($fp, LOCK_UN);
    fclose($fp);
    return $written;
}

/** Records the area from a public action=traffic request, without client IDs/IPs. */
function ltu_record_client_demand($query, $now = null, $file = null,
                                 $secret = null, $ttl = 20,
                                 $write_interval = 5, $max_regions = 128) {
    if ($secret === null) $secret = defined('LTU_TOKEN') ? LTU_TOKEN : '';
    $area = ltu_demand_area_from_query($query);
    if ($area === null || $secret === '') return false;
    $region = ltu_demand_region_for_area($area, $secret);
    if ($region === null) return false;
    return ltu_demand_store_region($region, $now, $file, $ttl,
                                   $write_interval, $max_regions);
}

/** Returns only fresh regions for the token-protected collector endpoint. */
function ltu_demand_active_regions($now = null, $file = null,
                                   $ttl = 20, $limit = 128) {
    if ($now === null) $now = time();
    if ($file === null) $file = ltu_demand_store_path();
    if (!is_file($file)) return array();
    $fp = @fopen($file, 'rb');
    if (!$fp) return array();
    if (!flock($fp, LOCK_SH)) {
        fclose($fp);
        return array();
    }
    $raw = stream_get_contents($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    $store = json_decode($raw, true);
    if (!is_array($store)) return array();

    $active = array();
    foreach ($store as $id => $region) {
        if (!is_array($region) || !isset($region['id'], $region['t'], $region['lat'],
                                        $region['lon'], $region['radius_km'])) continue;
        $t = intval($region['t']);
        $lat = floatval($region['lat']);
        $lon = floatval($region['lon']);
        $radius = intval($region['radius_km']);
        if (!preg_match('/^D[A-F0-9]{12}$/D', (string)$region['id'])
            || $t > $now || ($now - $t) > $ttl
            || !is_finite($lat) || !is_finite($lon)
            || $lat < -90.0 || $lat > 90.0 || $lon < -180.0 || $lon > 180.0
            || $radius < 1 || $radius > 463) continue;
        $active[] = array('id' => (string)$region['id'], 't' => $t,
                          'lat' => $lat, 'lon' => $lon, 'radius_km' => $radius);
    }
    usort($active, function ($a, $b) { return intval($b['t']) - intval($a['t']); });
    return array_slice($active, 0, max(0, intval($limit)));
}

/** Scrubs exact client-demand geometry from the unauthenticated status APIs. */
function ltu_public_region_meta($id, $region) {
    if (!is_array($region)) return $region;
    $is_demand = !empty($region['demand_driven'])
              || preg_match('/^D[A-F0-9]{12}$/D', (string)$id);
    if (!$is_demand) return $region;
    $public = array('demand_driven' => true, 'mode' => 'demand');
    foreach (array('t', 'count', 'source', 'source_max_age', 'hist_t', 'hist') as $key) {
        if (!array_key_exists($key, $region)) continue;
        if ($key === 'source' && !ltu_source_name_is_valid($region[$key])) continue;
        $public[$key] = $region[$key];
    }
    return $public;
}

/** Collector source identifiers accepted by the push/status API. */
function ltu_source_name_is_valid($source) {
    return is_scalar($source)
        && in_array((string)$source,
                    array('adsb.lol', 'adsb.fi', 'opensky', 'planetrack'), true);
}

/** Hides demand geometry and limits public source status to non-secret fields. */
function ltu_public_source_status($source_status) {
    if (!is_array($source_status)) return array();
    $public = array();
    foreach ($source_status as $id => $entry) {
        if (!is_array($entry)) continue;
        // Hide statuses left by the retired ADSB.lol global feed/tile raster.
        if (strpos((string)$id, 'global-fallback') === 0
            || (isset($entry['mode']) && $entry['mode'] === 'global-fallback')
            || ((string)$id === 'global' && isset($entry['source'], $entry['mode'])
                && $entry['source'] === 'adsb.lol' && $entry['mode'] === 'global')) {
            continue;
        }

        // Never pass collector credentials, arbitrary error text, or unknown
        // metadata through the unauthenticated health/status endpoints.
        $safe = array();
        $safe['source'] = ltu_source_name_is_valid(
            isset($entry['source']) ? $entry['source'] : null
        ) ? (string)$entry['source'] : 'adsb.lol';
        if (array_key_exists('ok', $entry)) $safe['ok'] = (bool)$entry['ok'];
        if (isset($entry['http']) && is_numeric($entry['http']))
            $safe['http'] = min(599, max(0, intval($entry['http'])));
        if (isset($entry['t']) && is_numeric($entry['t'])) $safe['t'] = intval($entry['t']);
        if (isset($entry['mode']) && is_scalar($entry['mode'])
            && in_array((string)$entry['mode'], array('global', 'regional', 'demand'), true)) {
            $safe['mode'] = (string)$entry['mode'];
        }
        if (isset($entry['max_age']) && is_numeric($entry['max_age'])
            && intval($entry['max_age']) > 0) {
            $safe['max_age'] = min(86400, max(5, intval($entry['max_age'])));
        }

        $is_demand = preg_match('/^D[A-F0-9]{12}(?:-(?:adsb-lol|adsb-fi|opensky|planetrack))?$/D',
                                (string)$id)
                  || (isset($entry['mode']) && $entry['mode'] === 'demand');
        if (!$is_demand) {
            // Also prevent a malformed collector region label from becoming a
            // credential side channel in the public JSON object key.
            $public_id = (string)$id;
            if (!in_array($public_id, array('global', 'opensky-global'), true)
                && preg_match('/^R[0-9]{2,}$/D', $public_id) !== 1) {
                $public_id = 'collector-source';
            }
            $public[$public_id] = $safe;
            continue;
        }
        $public_id = 'client-demand-' . $safe['source'];
        if (!isset($public[$public_id])
            || intval(isset($safe['t']) ? $safe['t'] : 0)
                > intval(isset($public[$public_id]['t']) ? $public[$public_id]['t'] : 0)) {
            $public[$public_id] = $safe;
        }
    }
    return $public;
}

/** Replaces demand region IDs in the public collector log with a generic label. */
function ltu_public_status_log($log) {
    if (!is_array($log)) return array();
    foreach ($log as &$entry) {
        if (is_array($entry) && isset($entry['region'])
            && preg_match('/^D[A-F0-9]{12}$/D', (string)$entry['region'])) {
            $entry['region'] = 'client-demand';
        }
    }
    unset($entry);
    return $log;
}

/** Private, shared-on-this-host state for an explicitly requested global OpenSky snapshot. */
function ltu_global_search_store_path($secret = null, $scope = null) {
    $demand_path = ltu_demand_store_path($secret, $scope);
    return str_replace('ltu-demand-', 'ltu-global-search-', $demand_path);
}

/**
 * Record a global-search request, coalescing concurrent clients and enforcing
 * a host-wide upstream cooldown. This stores timestamps only, never queries or
 * client identifiers.
 */
function ltu_global_search_request($now = null, $file = null, $secret = null,
                                  $scope = null, $ttl = 180,
                                  $min_interval = 120) {
    if ($now === null) $now = time();
    if ($file === null) $file = ltu_global_search_store_path($secret, $scope);
    $ttl = max(1, min(3600, intval($ttl)));
    $min_interval = max(1, min(86400, intval($min_interval)));
    $fp = @fopen($file, 'c+b');
    if (!$fp) return array('pending' => false, 'rate_limited' => false,
                           'retry_after' => 0, 'error' => 'state_unavailable');
    @chmod($file, 0600);
    if (!flock($fp, LOCK_EX)) {
        fclose($fp);
        return array('pending' => false, 'rate_limited' => false,
                     'retry_after' => 0, 'error' => 'state_unavailable');
    }
    rewind($fp);
    $state = json_decode(stream_get_contents($fp), true);
    if (!is_array($state)) $state = array();
    $requested = isset($state['requested_at']) ? intval($state['requested_at']) : 0;
    $completed = isset($state['completed_at']) ? intval($state['completed_at']) : 0;
    $last_request = isset($state['last_request_at'])
                  ? intval($state['last_request_at']) : 0;
    if ($requested > $now || $completed > $now || $last_request > $now) {
        $requested = $completed = $last_request = 0;
    }

    $pending = $requested > $completed && ($now - $requested) <= $ttl;
    $rate_limited = false;
    $retry_after = 0;
    if (!$pending) {
        $elapsed = $last_request > 0 ? $now - $last_request : $min_interval;
        if ($elapsed < $min_interval) {
            $rate_limited = true;
            $retry_after = $min_interval - $elapsed;
        } else {
            $requested = $last_request = intval($now);
            $pending = true;
        }
    }
    $state = array('requested_at' => $requested,
                   'completed_at' => $completed,
                   'last_request_at' => $last_request);
    $json = json_encode($state);
    $written = $json !== false && ftruncate($fp, 0) && rewind($fp)
            && fwrite($fp, $json) === strlen($json) && fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    if (!$written) return array('pending' => false, 'rate_limited' => false,
                                'retry_after' => 0, 'error' => 'state_unavailable');
    return array('pending' => $pending, 'rate_limited' => $rate_limited,
                 'retry_after' => $retry_after, 'error' => null);
}

/** True only while an unserved user-triggered global-search request is fresh. */
function ltu_global_search_is_pending($now = null, $file = null,
                                      $secret = null, $scope = null,
                                      $ttl = 180) {
    if ($now === null) $now = time();
    if ($file === null) $file = ltu_global_search_store_path($secret, $scope);
    $ttl = max(1, min(3600, intval($ttl)));
    $fp = @fopen($file, 'rb');
    if (!$fp) return false;
    if (!flock($fp, LOCK_SH)) {
        fclose($fp);
        return false;
    }
    $state = json_decode(stream_get_contents($fp), true);
    flock($fp, LOCK_UN);
    fclose($fp);
    if (!is_array($state)) return false;
    $requested = isset($state['requested_at']) ? intval($state['requested_at']) : 0;
    $completed = isset($state['completed_at']) ? intval($state['completed_at']) : 0;
    return $requested > $completed && $requested > 0 && $requested <= $now
        && ($now - $requested) <= $ttl;
}

/** Mark the current request served once the collector has pushed its OpenSky snapshot. */
function ltu_global_search_complete($now = null, $file = null,
                                   $secret = null, $scope = null) {
    if ($now === null) $now = time();
    if ($file === null) $file = ltu_global_search_store_path($secret, $scope);
    $fp = @fopen($file, 'c+b');
    if (!$fp) return false;
    @chmod($file, 0600);
    if (!flock($fp, LOCK_EX)) {
        fclose($fp);
        return false;
    }
    rewind($fp);
    $state = json_decode(stream_get_contents($fp), true);
    if (!is_array($state)) $state = array();
    $requested = isset($state['requested_at']) ? intval($state['requested_at']) : 0;
    $completed = isset($state['completed_at']) ? intval($state['completed_at']) : 0;
    $state['completed_at'] = max($completed, min(intval($now), $requested));
    $json = json_encode(array(
        'requested_at' => $requested,
        'completed_at' => $state['completed_at'],
        'last_request_at' => isset($state['last_request_at'])
                           ? intval($state['last_request_at']) : 0,
    ));
    $written = $json !== false && ftruncate($fp, 0) && rewind($fp)
            && fwrite($fp, $json) === strlen($json) && fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    return $written;
}
