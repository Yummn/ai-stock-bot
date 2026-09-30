#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <string>
#include <thread>

#include "emt_trader_api.h"

namespace {

const char *Env(const char *name) {
    const char *value = std::getenv(name);
    return value ? value : "";
}

class AssetProbe final : public EMT::API::TraderSpi {
public:
    std::atomic<bool> done{false};
    std::atomic<bool> success{false};

    void OnQueryAsset(EMTQueryAssetRsp *asset, EMTRI *error, int,
                      bool is_last, uint64_t) override {
        if (error && error->error_id != 0) {
            std::cerr << "Asset query failed, code " << error->error_id << '\n';
        } else if (asset) {
            success = true;
        }
        if (is_last) done = true;
    }

    void OnDisconnected(int reason) override {
        std::cerr << "Disconnected, code " << reason << '\n';
        done = true;
    }
};

}  // namespace

int main(int argc, char **argv) {
    if (argc != 2 || (std::string(argv[1]) != "--version" &&
                      std::string(argv[1]) != "--probe")) {
        std::cerr << "Usage: emt_readonly_probe.exe --version|--probe\n";
        return 2;
    }

    const bool probe = std::string(argv[1]) == "--probe";
    if (probe && std::string(Env("EMT_TEST_ENV")) != "simulation") {
        std::cerr << "Set EMT_TEST_ENV=simulation for the test API only.\n";
        return 2;
    }
    const std::string ip = Env("EMT_TEST_IP");
    const std::string account = Env("EMT_TEST_ACCOUNT");
    const std::string port_text = Env("EMT_TEST_PORT");
    int port = 0;
    try {
        if (!port_text.empty()) port = std::stoi(port_text);
    } catch (...) {
        std::cerr << "Invalid EMT_TEST_PORT.\n";
        return 2;
    }
    if (probe && (ip.empty() || account.empty() || port < 1 || port > 65535)) {
        std::cerr << "Missing EMT_TEST_IP, EMT_TEST_PORT, or EMT_TEST_ACCOUNT. "
                     "Use the API test details supplied by the broker.\n";
        return 2;
    }

    std::filesystem::create_directories(".tools/emt_api_cpp/probe_state");
    auto *api = EMT::API::TraderApi::CreateTraderApi(
        120, "./.tools/emt_api_cpp/probe_state", EMT_LOG_LEVEL_INFO);
    if (!api) {
        std::cerr << "Could not load EMT TraderApi.\n";
        return 1;
    }
    std::cout << "EMT API version: " << api->GetApiVersion() << '\n';
    if (!probe) {
        api->Release();
        return 0;
    }

    AssetProbe callback;
    api->RegisterSpi(&callback);
    const uint64_t session = api->Login(
        ip.c_str(), port, account.c_str(), Env("EMT_TEST_PASSWORD"),
        EMT_PROTOCOL_TCP);
    if (!session) {
        const auto *error = api->GetApiLastError();
        std::cerr << "Simulation API login failed, code "
                  << (error ? error->error_id : -1) << '\n';
        api->Release();
        return 1;
    }
    std::cout << "Simulation API login succeeded. Querying asset status only.\n";
    const int result = api->QueryAsset(session, 1);
    if (result != 0) {
        const auto *error = api->GetApiLastError();
        std::cerr << "Asset query request failed, code "
                  << (error ? error->error_id : result) << '\n';
    } else {
        const auto deadline = std::chrono::steady_clock::now() +
                              std::chrono::seconds(10);
        while (!callback.done && std::chrono::steady_clock::now() < deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        std::cout << (callback.success ? "Asset query succeeded.\n"
                                      : "Asset query did not succeed.\n");
    }
    api->Logout(session);
    api->Release();
    return result == 0 && callback.success ? 0 : 1;
}
