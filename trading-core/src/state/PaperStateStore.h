#pragma once

#include "../risk/RiskManager.h"
#include <string>

namespace persistence {

bool save_live_lih_state(risk::RiskManager& rm, const std::string& path, bool shadow_mode = false);
bool load_live_lih_state(risk::RiskManager& rm, const std::string& path, bool shadow_mode = false);

} // namespace persistence
