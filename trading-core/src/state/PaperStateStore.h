#pragma once
#include <string>
#include "../risk/RiskManager.h"

namespace persistence {

// Save / load paper-trading RiskManager state to JSON (survives container restarts).
bool save_paper_state(const risk::RiskManager& rm, const std::string& path);
bool load_paper_state(risk::RiskManager& rm, const std::string& path);

// Live LIH open rounds + history (survives bot restarts; prevents duplicate LEG1).
bool save_live_lih_state(const risk::RiskManager& rm, const std::string& path, bool shadow_mode = false);
bool load_live_lih_state(risk::RiskManager& rm, const std::string& path, bool shadow_mode = false);

} // namespace persistence
