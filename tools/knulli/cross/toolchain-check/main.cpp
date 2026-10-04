#include <boost/locale.hpp>
#include <boost/program_options.hpp>

int main()
{
    boost::locale::generator generator;
    boost::program_options::options_description options("toolchain check");
    return options.options().empty() ? 0 : 1;
}
