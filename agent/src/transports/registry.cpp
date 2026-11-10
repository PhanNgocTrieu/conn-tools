#include <memory>
#include <vector>

#include "ctt/transport.hpp"

namespace ctt {

std::unique_ptr<Transport> make_tcp_transport();
// Phase 2+: make_rfcomm_transport(), make_l2cap_transport(), ...

namespace {
const std::vector<std::unique_ptr<Transport>>& registry() {
    static const std::vector<std::unique_ptr<Transport>> r = [] {
        std::vector<std::unique_ptr<Transport>> v;
        v.push_back(make_tcp_transport());
        return v;
    }();
    return r;
}
}  // namespace

Transport* find_transport(std::string_view name) {
    for (const auto& t : registry())
        if (name == t->name()) return t.get();
    return nullptr;
}

std::vector<Transport*> all_transports() {
    std::vector<Transport*> v;
    for (const auto& t : registry()) v.push_back(t.get());
    return v;
}

}  // namespace ctt
