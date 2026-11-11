<?php
/**
 * Short-lived per-aircraft ADS-B paths shared through the proxy.
 *
 * Track files live in a token-scoped directory under the system temp path,
 * outside the public web root. A single compressed file per ICAO hex keeps
 * concurrent collector pushes from rewriting one large global JSON document.
 */

if (!defined('LTU_TRACK_RETENTION_SEC')) define('LTU_TRACK_RETENTION_SEC', 3600);
if (!defined('LTU_TRACK_SAMPLE_INTERVAL_SEC')) define('LTU_TRACK_SAMPLE_INTERVAL_SEC', 10);
if (!defined('LTU_TRACK_MAX_POINTS')) define('LTU_TRACK_MAX_POINTS', 360);
if (!defined('LTU_TRACK_GC_INTERVAL_SEC')) define('LTU_TRACK_GC_INTERVAL_SEC', 300);
if (!defined('LTU_TRACK_ALIAS_REFRESH_SEC')) define('LTU_TRACK_ALIAS_REFRESH_SEC', 60);
if (!defined('LTU_TRACK_MAX_AIRCRAFT')) define('LTU_TRACK_MAX_AIRCRAFT', 20000);

function ltu_track_store_directory($secret = null, $scope = null) {
    if ($secret === null) $secret = defined('LTU_TOKEN') ? LTU_TOKEN : '';
    if ($scope === null) $scope = __DIR__;
    $base = rtrim(sys_get_temp_dir(), DIRECTORY_SEPARATOR);
    $key = substr(hash('sha256', (string)$scope . "\0" . (string)$secret), 0, 24);
    return $base . DIRECTORY_SEPARATOR . 'ltu-tracks-' . $key;
}

function ltu_track_ensure_directory($directory = null) {
    static $ready = array();
    if ($directory === null) $directory = ltu_track_store_directory();
    if (isset($ready[$directory]) && is_dir($directory)) return true;
    if (!is_dir($directory) && !@mkdir($directory, 0700, true) && !is_dir($directory)) {
        return false;
    }
    @chmod($directory, 0700);
    $ready[$directory] = true;
    return true;
}

function ltu_track_file($hex, $directory = null) {
    $hex = strtolower(trim((string)$hex));
    if (!preg_match('/^[a-f0-9]{1,16}$/D', $hex)) return null;
    if ($directory === null) $directory = ltu_track_store_directory();
    return rtrim($directory, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . $hex . '.json.gz';
}

function ltu_track_alias_file($flight, $directory = null) {
    $flight = strtoupper(trim((string)$flight));
    if (!preg_match('/^[A-Z0-9-]{1,16}$/D', $flight)) return null;
    if ($directory === null) $directory = ltu_track_store_directory();
    $key = substr(hash('sha256', $flight), 0, 24);
    return rtrim($directory, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR
         . 'flight_' . $key . '.json';
}

function ltu_track_distance_km($lat1, $lon1, $lat2, $lon2) {
    $earth = 6371.0;
    $p1 = deg2rad(floatval($lat1));
    $p2 = deg2rad(floatval($lat2));
    $dp = deg2rad(floatval($lat2) - floatval($lat1));
    $dl = deg2rad(floatval($lon2) - floatval($lon1));
    $a = sin($dp / 2.0) * sin($dp / 2.0)
       + cos($p1) * cos($p2) * sin($dl / 2.0) * sin($dl / 2.0);
    return 2.0 * $earth * asin(min(1.0, sqrt(max(0.0, $a))));
}

function ltu_track_point_from_aircraft($aircraft, $now) {
    if (!is_array($aircraft) || !isset($aircraft['hex'], $aircraft['lat'], $aircraft['lon'])
        || !is_scalar($aircraft['hex']) || !is_numeric($aircraft['lat'])
        || !is_numeric($aircraft['lon'])) return null;

    $hex = strtolower(trim((string)$aircraft['hex']));
    $lat = floatval($aircraft['lat']);
    $lon = floatval($aircraft['lon']);
    if (!preg_match('/^[a-f0-9]{1,16}$/D', $hex)
        || !is_finite($lat) || !is_finite($lon)
        || $lat < -90.0 || $lat > 90.0 || $lon < -180.0 || $lon > 180.0) return null;

    $alt = isset($aircraft['alt_baro']) ? $aircraft['alt_baro']
         : (isset($aircraft['alt_geom']) ? $aircraft['alt_geom'] : null);
    if (is_scalar($alt) && strtolower(trim((string)$alt)) === 'ground') {
        $alt = 'ground';
    } elseif (is_numeric($alt) && is_finite(floatval($alt))) {
        $alt = intval(round(floatval($alt)));
    } else {
        $alt = null;
    }

    $track = isset($aircraft['track']) && is_numeric($aircraft['track'])
           && is_finite(floatval($aircraft['track']))
           ? round(floatval($aircraft['track']), 1) : null;
    $flight = isset($aircraft['flight']) && is_scalar($aircraft['flight'])
            ? strtoupper(trim((string)$aircraft['flight'])) : '';

    return array(
        'hex' => $hex,
        'flight' => substr($flight, 0, 16),
        'point' => array(
            't' => intval($now),
            'lat' => round($lat, 5),
            'lon' => round($lon, 5),
            'alt' => $alt,
            'track' => $track,
        ),
    );
}

function ltu_track_decode_file($raw) {
    if (!is_string($raw) || $raw === '') return array();
    $json = @gzdecode($raw);
    if ($json === false) return array();
    $data = json_decode($json, true);
    return is_array($data) ? $data : array();
}

/** Add or refresh one aircraft position. Invalid or unidentified aircraft are ignored. */
function ltu_track_record_aircraft($aircraft, $now = null, $directory = null) {
    if ($now === null) $now = time();
    $entry = ltu_track_point_from_aircraft($aircraft, $now);
    if ($entry === null) return false;
    if ($directory === null) $directory = ltu_track_store_directory();
    if (!ltu_track_ensure_directory($directory)) return false;

    $file = ltu_track_file($entry['hex'], $directory);
    if ($file === null) return false;
    if (abs(time() - intval($now)) <= 60) {
        $modified = @filemtime($file);
        if ($modified !== false && intval($now) - $modified < LTU_TRACK_SAMPLE_INTERVAL_SEC) {
            return true; // avoid opening/compressing the same track on rapid repeat pushes
        }
    }
    $fp = @fopen($file, 'c+b');
    if (!$fp) return false;
    @chmod($file, 0600);
    if (!flock($fp, LOCK_EX)) {
        fclose($fp);
        return false;
    }

    rewind($fp);
    $data = ltu_track_decode_file(stream_get_contents($fp));
    $points = isset($data['points']) && is_array($data['points']) ? $data['points'] : array();
    $cutoff = intval($now) - LTU_TRACK_RETENTION_SEC;
    $kept = array();
    foreach ($points as $point) {
        if (!is_array($point) || !isset($point['t'], $point['lat'], $point['lon'])
            || !is_numeric($point['t']) || !is_numeric($point['lat'])
            || !is_numeric($point['lon'])) continue;
        $point_time = intval($point['t']);
        if ($point_time >= $cutoff && $point_time <= intval($now) + 5) $kept[] = $point;
    }
    $points = $kept;
    $changed = count($points) !== (isset($data['points']) && is_array($data['points'])
                                  ? count($data['points']) : 0);
    $new_point = $entry['point'];
    $last = $points ? $points[count($points) - 1] : null;

    if ($last === null) {
        $points[] = $new_point;
        $changed = true;
    } else {
        $elapsed = intval($now) - intval($last['t']);
        if ($elapsed >= LTU_TRACK_SAMPLE_INTERVAL_SEC) {
            $distance = ltu_track_distance_km(
                floatval($last['lat']), floatval($last['lon']),
                floatval($new_point['lat']), floatval($new_point['lon']));
            if ($distance < 0.05) {
                // Keep an aircraft that remains nearby available without
                // filling its path with identical stationary positions.
                $points[count($points) - 1] = $new_point;
            } else {
                $points[] = $new_point;
            }
            $changed = true;
        }
    }

    if (count($points) > LTU_TRACK_MAX_POINTS) {
        $points = array_slice($points, -LTU_TRACK_MAX_POINTS);
        $changed = true;
    }
    if ($changed || !isset($data['hex'])) {
        $stored = array(
            'hex' => $entry['hex'],
            'flight' => $entry['flight'] !== '' ? $entry['flight']
                     : (isset($data['flight']) ? $data['flight'] : ''),
            'updated' => intval($now),
            'points' => $points,
        );
        $json = json_encode($stored, JSON_UNESCAPED_SLASHES);
        $compressed = ($json === false) ? false : gzencode($json, 6);
        $written = false;
        if ($compressed !== false && ftruncate($fp, 0) && rewind($fp)) {
            $written = fwrite($fp, $compressed) === strlen($compressed) && fflush($fp);
        }
    } else {
        $written = true;
    }

    flock($fp, LOCK_UN);
    fclose($fp);
    return $written;
}

/** Store a hashed callsign-to-hex lookup so a typed flight can preload a trail. */
function ltu_track_record_alias($flight, $hex, $now = null, $directory = null) {
    if ($now === null) $now = time();
    $path = ltu_track_alias_file($flight, $directory);
    $track_path = ltu_track_file($hex, $directory);
    if ($path === null || $track_path === null) return false;
    if ($directory === null) $directory = ltu_track_store_directory();
    if (!ltu_track_ensure_directory($directory)) return false;

    if (abs(time() - intval($now)) <= 60) {
        $modified = @filemtime($path);
        if ($modified !== false && intval($now) - $modified < LTU_TRACK_ALIAS_REFRESH_SEC) {
            return true;
        }
    }
    $fp = @fopen($path, 'c+b');
    if (!$fp) return false;
    @chmod($path, 0600);
    if (!flock($fp, LOCK_EX)) {
        fclose($fp);
        return false;
    }
    rewind($fp);
    $raw = stream_get_contents($fp);
    $previous = json_decode($raw === false ? '' : $raw, true);
    $flight = strtoupper(trim((string)$flight));
    $hex = strtolower(trim((string)$hex));
    if (is_array($previous) && isset($previous['hex'], $previous['t'])
        && strtolower((string)$previous['hex']) === $hex
        && intval($now) - intval($previous['t']) < LTU_TRACK_ALIAS_REFRESH_SEC) {
        flock($fp, LOCK_UN);
        fclose($fp);
        return true;
    }
    $json = json_encode(array('flight' => $flight, 'hex' => $hex, 't' => intval($now)));
    $written = false;
    if ($json !== false && ftruncate($fp, 0) && rewind($fp)) {
        $written = fwrite($fp, $json) === strlen($json) && fflush($fp);
    }
    flock($fp, LOCK_UN);
    fclose($fp);
    return $written;
}

/** Record positions from a collector push, relay push, or live traffic response. */
function ltu_track_record_batch($aircraft, $now = null, $directory = null) {
    if ($now === null) $now = time();
    if (!is_array($aircraft)) return 0;
    $stored = 0;
    foreach ($aircraft as $entry) {
        $track_entry = ltu_track_point_from_aircraft($entry, $now);
        if ($track_entry === null) continue;
        if (!ltu_track_record_aircraft($entry, $now, $directory)) continue;
        ++$stored;
        if ($track_entry['flight'] !== '') {
            ltu_track_record_alias($track_entry['flight'], $track_entry['hex'],
                                   $now, $directory);
        }
    }
    return $stored;
}

/** Load only the last hour of a path; returns null when the aircraft has no track. */
function ltu_track_load($hex, $now = null, $directory = null) {
    if ($now === null) $now = time();
    if ($directory === null) $directory = ltu_track_store_directory();
    $file = ltu_track_file($hex, $directory);
    if ($file === null || !is_file($file)) return null;
    $fp = @fopen($file, 'rb');
    if (!$fp) return null;
    if (!flock($fp, LOCK_SH)) {
        fclose($fp);
        return null;
    }
    $data = ltu_track_decode_file(stream_get_contents($fp));
    flock($fp, LOCK_UN);
    fclose($fp);
    if (!isset($data['points']) || !is_array($data['points'])) return null;

    $cutoff = intval($now) - LTU_TRACK_RETENTION_SEC;
    $points = array();
    foreach ($data['points'] as $point) {
        if (!is_array($point) || !isset($point['t'], $point['lat'], $point['lon'])
            || !is_numeric($point['t']) || !is_numeric($point['lat'])
            || !is_numeric($point['lon'])) continue;
        $point_time = intval($point['t']);
        if ($point_time < $cutoff || $point_time > intval($now) + 5) continue;
        $points[] = $point;
    }

    return array(
        'hex' => strtolower(trim((string)$hex)),
        'flight' => isset($data['flight']) ? (string)$data['flight'] : '',
        'updated' => isset($data['updated']) ? intval($data['updated']) : 0,
        'points' => $points,
    );
}

/** Resolve a current/previous callsign to its ICAO-hex track. */
function ltu_track_load_flight($flight, $now = null, $directory = null) {
    if ($now === null) $now = time();
    if ($directory === null) $directory = ltu_track_store_directory();
    $file = ltu_track_alias_file($flight, $directory);
    if ($file === null || !is_file($file)) return null;
    $fp = @fopen($file, 'rb');
    if (!$fp) return null;
    if (!flock($fp, LOCK_SH)) {
        fclose($fp);
        return null;
    }
    $raw = stream_get_contents($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    $alias = json_decode($raw === false ? '' : $raw, true);
    if (!is_array($alias) || !isset($alias['hex'], $alias['t'])
        || !is_numeric($alias['t'])
        || intval($alias['t']) < intval($now) - LTU_TRACK_RETENTION_SEC) return null;
    return ltu_track_load($alias['hex'], $now, $directory);
}

/** Periodically remove stale files and cap the number of retained aircraft. */
function ltu_track_prune_expired($now = null, $directory = null) {
    if ($now === null) $now = time();
    if ($directory === null) $directory = ltu_track_store_directory();
    if (!is_dir($directory)) return 0;

    $lock_path = rtrim($directory, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . '.prune.lock';
    $lock = @fopen($lock_path, 'c+b');
    if (!$lock || !flock($lock, LOCK_EX | LOCK_NB)) {
        if ($lock) fclose($lock);
        return 0;
    }
    @chmod($lock_path, 0600);
    $marker = rtrim($directory, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . '.last-prune';
    $last_prune = @filemtime($marker);
    if ($last_prune !== false && intval($now) - $last_prune < LTU_TRACK_GC_INTERVAL_SEC) {
        flock($lock, LOCK_UN);
        fclose($lock);
        return 0;
    }

    $files = glob(rtrim($directory, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . '*.json.gz');
    $active = array();
    $removed = 0;
    foreach ((array)$files as $file) {
        $mtime = @filemtime($file);
        if ($mtime === false || intval($now) - $mtime > LTU_TRACK_RETENTION_SEC) {
            if (@unlink($file)) ++$removed;
            continue;
        }
        $active[] = array('file' => $file, 'mtime' => $mtime);
    }
    $aliases = glob(rtrim($directory, DIRECTORY_SEPARATOR)
                    . DIRECTORY_SEPARATOR . 'flight_*.json');
    foreach ((array)$aliases as $file) {
        $mtime = @filemtime($file);
        if ($mtime === false || intval($now) - $mtime > LTU_TRACK_RETENTION_SEC) {
            if (@unlink($file)) ++$removed;
        }
    }
    if (count($active) > LTU_TRACK_MAX_AIRCRAFT) {
        usort($active, function ($a, $b) { return $a['mtime'] <=> $b['mtime']; });
        $excess = count($active) - LTU_TRACK_MAX_AIRCRAFT;
        for ($i = 0; $i < $excess; ++$i) {
            if (@unlink($active[$i]['file'])) ++$removed;
        }
    }
    @touch($marker, intval($now));
    @chmod($marker, 0600);
    flock($lock, LOCK_UN);
    fclose($lock);
    return $removed;
}
