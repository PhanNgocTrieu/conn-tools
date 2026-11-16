// Thread-safe event output: human readable text or JSON lines (for the
// Python orchestrator).
#pragma once

#include <string>

#include "ctt/json.hpp"

namespace ctt::out {

enum class Format { Text, Json };

void set_format(Format f);
Format format();

// Prints `j` (JSON mode) or `text` (text mode; skipped when empty).
void emit(const Json& j, const std::string& text);

std::string strprintf(const char* fmt, ...) __attribute__((format(printf, 1, 2)));

}  // namespace ctt::out
