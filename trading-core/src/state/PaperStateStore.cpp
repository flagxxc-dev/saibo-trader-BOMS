#include "PaperStateStore.h"
#include <spdlog/spdlog.h>
#include <boost/json.hpp>
#include <fstream>
#include <filesystem>

namespace persistence {

namespace fs = std::filesystem;

bool save_paper_state(const risk::RiskManager& rm, const std::string& path) {
    try {
        auto doc = rm.export_paper_state();
        std::string json = boost::json::serialize(doc);

        fs::path p(path);
        if (p.has_parent_path()) {
            fs::create_directories(p.parent_path());
        }

        std::string tmp = path + ".tmp";
        {
            std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
            if (!out) {
                spdlog::warn("Paper state: cannot write {}", tmp);
                return false;
            }
            out << json;
        }
        fs::rename(tmp, path);
        return true;
    } catch (const std::exception& e) {
        spdlog::warn("Paper state save failed: {}", e.what());
        return false;
    }
}

bool load_paper_state(risk::RiskManager& rm, const std::string& path) {
    try {
        if (!fs::exists(path)) return false;

        std::ifstream in(path, std::ios::binary);
        if (!in) return false;

        std::string content((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
        if (content.empty()) return false;

        boost::json::value jv = boost::json::parse(content);
        if (!jv.is_object()) return false;

        if (!rm.import_paper_state(jv.as_object())) return false;

        spdlog::info("Paper state restored from {}", path);
        return true;
    } catch (const std::exception& e) {
        spdlog::warn("Paper state load failed ({}): {}", path, e.what());
        return false;
    }
}

bool save_live_lih_state(const risk::RiskManager& rm, const std::string& path, bool shadow_mode) {
    try {
        auto doc = rm.export_live_lih_state();
        if (shadow_mode) {
            doc["open_lih_positions"] = boost::json::object{};
        }
        std::string json = boost::json::serialize(doc);

        fs::path p(path);
        if (p.has_parent_path()) {
            fs::create_directories(p.parent_path());
        }

        std::string tmp = path + ".tmp";
        {
            std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
            if (!out) {
                spdlog::warn("Live LIH state: cannot write {}", tmp);
                return false;
            }
            out << json;
        }
        fs::rename(tmp, path);
        return true;
    } catch (const std::exception& e) {
        spdlog::warn("Live LIH state save failed: {}", e.what());
        return false;
    }
}

bool load_live_lih_state(risk::RiskManager& rm, const std::string& path, bool shadow_mode) {
    try {
        if (!fs::exists(path)) return false;

        std::ifstream in(path, std::ios::binary);
        if (!in) return false;

        std::string content((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
        if (content.empty()) return false;

        boost::json::value jv = boost::json::parse(content);
        if (!jv.is_object()) return false;

        if (!rm.import_live_lih_state(jv.as_object())) return false;

        if (shadow_mode) {
            rm.clear_open_lih_positions();
            spdlog::info("Shadow mode: open LIH rows not restored from disk (simulation only)");
        } else {
            spdlog::info("Live LIH state restored from {}", path);
        }
        return true;
    } catch (const std::exception& e) {
        spdlog::warn("Live LIH state load failed ({}): {}", path, e.what());
        return false;
    }
}

} // namespace persistence
