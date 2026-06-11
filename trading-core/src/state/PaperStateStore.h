#pragma once
#include <string>
#include "../risk/RiskManager.h"

namespace persistence {

// Save / load paper-trading RiskManager state to JSON (survives container restarts).
bool save_paper_state(const risk::RiskManager& rm, const std::string& path);
bool load_paper_state(risk::RiskManager& rm, const std::string& path);

} // namespace persistence
