#include "../proxy/opensky_auth.h"

#include <cassert>
#include <iostream>
#include <string>

int main() {
    std::string client_id, client_secret;
    const std::string credentials =
        "{\"clientId\": \"ltu-proxy\", \"clientSecret\": \"s3cr3t\\\"value\\\\end\"}";
    assert(ltu::opensky::read_credentials_json(credentials, client_id, client_secret));
    assert(client_id == "ltu-proxy");
    assert(client_secret == "s3cr3t\"value\\end");

    std::string access_token;
    assert(ltu::opensky::json_string_field(
        "{\"access_token\":\"eyJhbGciOi.abc\",\"token_type\":\"Bearer\"}",
        "access_token", access_token));
    assert(access_token == "eyJhbGciOi.abc");

    long long expires = 0;
    assert(ltu::opensky::json_integer_field(
        "{ \"expires_in\" : 1800, \"x\": 1 }", "expires_in", expires));
    assert(expires == 1800);
    assert(!ltu::opensky::json_integer_field("{\"expires_in\":null}", "expires_in", expires));
    assert(!ltu::opensky::read_credentials_json("{\"clientId\":\"only-id\"}", client_id, client_secret));
    assert(ltu::opensky::read_credentials_json(
        "{\"client_id\":\"snake-id\",\"client_secret\":\"snake-secret\"}",
        client_id, client_secret));
    assert(client_id == "snake-id" && client_secret == "snake-secret");

    assert(ltu::opensky::form_url_encode("a b+c&d") == "a+b%2Bc%26d");
    assert(ltu::opensky::form_url_encode("client_id") == "client_id");

    std::string unicode;
    assert(ltu::opensky::json_string_field("{\"value\":\"\\u00e4\"}", "value", unicode));
    assert(unicode == "\xc3\xa4");

    std::cout << "OpenSky OAuth helper checks passed\n";
    return 0;
}
