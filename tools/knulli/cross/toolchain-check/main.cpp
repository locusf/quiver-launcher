#include <boost/locale.hpp>
#include <boost/program_options.hpp>
#include "host-generated.h"

static_assert(HOST_GENERATED_VALUE == 42, "The native generator must supply the target build's header.");

int main()
{
    boost::locale::generator generator;
    boost::program_options::options_description options("toolchain check");
    return options.options().empty() ? 0 : 1;
}
