<?php
// Exercise the production OpenSky normalizer without including the HTTP endpoint.
$source = file_get_contents(__DIR__ . '/../proxy/index.php');
$start = strpos($source, 'function ltu_opensky_to_adsb(');
if ($start === false) throw new RuntimeException('OpenSky normalizer not found');
$open = strpos($source, '{', $start);
if ($open === false) throw new RuntimeException('OpenSky normalizer body not found');
$depth = 0;
$end = false;
for ($i = $open, $n = strlen($source); $i < $n; ++$i) {
    if ($source[$i] === '{') ++$depth;
    elseif ($source[$i] === '}' && --$depth === 0) { $end = $i + 1; break; }
}
if ($end === false) throw new RuntimeException('OpenSky normalizer is not balanced');

define('LTU_MAX_AC', 30000);
eval(substr($source, $start, $end - $start));

$payload = array('states' => array(
    array('abc123', ' ab123  ', 'United States', 1, 1, -71.0, 42.0,
          1000.0, false, 200.0, 90.5, 1.0, null, 1100.0, '7700', false, 0),
    array('def456', 'ground', 'United States', 1, 1, 10.0, 20.0,
          null, true, 0.0, null, null, null, null, null, false, 0),
    array('ghi789', 'geo only', 'United States', 1, 1, 11.0, 21.0,
          null, false, 20.0, 180.0, null, null, 900.0, null, false, 0),
    array('bad', 'no position', 'Unknown', 1, 1, null, null,
          null, false, null, null, null, null, null, null, false, 0)
));
$result = ltu_opensky_to_adsb($payload);
if (!is_array($result) || count($result['ac']) !== 3) {
    throw new RuntimeException('Expected three positioned aircraft');
}
$first = $result['ac'][0];
if ($first['hex'] !== 'abc123' || $first['flight'] !== 'AB123'
    || $first['lat'] !== 42.0 || $first['lon'] !== -71.0
    || $first['alt_baro'] !== 3281 || $first['alt_geom'] !== 3609
    || $first['gs'] !== 388.8
    || $first['track'] !== 90.5 || $first['baro_rate'] !== 197
    || $first['squawk'] !== '7700' || $first['src'] !== 'opensky') {
    throw new RuntimeException('OpenSky units/fields were not normalized correctly');
}
if ($result['ac'][1]['alt_baro'] !== 'ground'
    || $result['ac'][1]['alt_geom'] !== null) {
    throw new RuntimeException('Ground altitude was not preserved');
}
if ($result['ac'][2]['alt_baro'] !== null
    || $result['ac'][2]['alt_geom'] !== 2953) {
    throw new RuntimeException('Geometric altitude was incorrectly mapped to barometric altitude');
}
if (ltu_opensky_to_adsb(array('states' => 'invalid')) !== null
    || ltu_opensky_to_adsb(array()) !== null) {
    throw new RuntimeException('Invalid OpenSky payload should be rejected');
}
echo "OpenSky PHP normalization checks passed\n";
