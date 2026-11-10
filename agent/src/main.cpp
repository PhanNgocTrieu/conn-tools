#include <csignal>
#include <cstdio>
#include <exception>
#include <string>

#include "ctt/i2c.hpp"
#include "ctt/output.hpp"
#include "ctt/session.hpp"
#include "ctt/version.hpp"

using namespace ctt;

int main(int argc, char** argv) {
    std::signal(SIGPIPE, SIG_IGN);
    if (argc >= 2 && std::string(argv[1]) == "i2c") return run_i2c_command(argc, argv);

    AgentOptions o;
    std::string err;
    if (!parse_args(argc, argv, o, err)) {
        std::fprintf(stderr, "ctt-agent: %s\n\n", err.c_str());
        print_usage(stderr);
        return 64;
    }
    out::set_format(o.json ? out::Format::Json : out::Format::Text);

    try {
        switch (o.role) {
            case Role::Server:
                return run_server(o);
            case Role::Client:
                return run_client(o);
            case Role::Version: {
                Json j;
                j.add("event", "version")
                    .add("version", CTT_VERSION)
                    .add("ctp", int(ctp::kVersion))
                    .add("arch", CTT_ARCH);
                out::emit(j, std::string("ctt-agent ") + CTT_VERSION + " (CTP v" +
                                 std::to_string(ctp::kVersion) + ", " + CTT_ARCH + ")");
                return 0;
            }
            case Role::ListTransports:
                for (Transport* t : all_transports()) {
                    Json j;
                    j.add("event", "transport").add("name", t->name()).add("description",
                                                                            t->description());
                    out::emit(j, std::string(t->name()) + "\t" + t->description());
                }
                return 0;
            case Role::Help:
                print_usage(stdout);
                return 0;
            case Role::None:
                break;
        }
    } catch (const std::exception& e) {
        Json j;
        j.add("event", "error").add("message", e.what());
        if (o.json) out::emit(j, "");
        std::fprintf(stderr, "ctt-agent: error: %s\n", e.what());
        return 1;
    }
    return 64;
}
