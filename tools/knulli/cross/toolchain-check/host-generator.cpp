#include <fstream>
#include <iostream>
#include <numeric>
#include <vector>

#ifndef __x86_64__
#error The code generator must be compiled for the x86-64 build host.
#endif

int main(int argc, char **argv)
{
    if (argc != 2)
    {
        std::cerr << "Expected an output header path.\n";
        return 1;
    }
    const std::vector<int> values{20, 22};
    std::ofstream output(argv[1]);
    output << "#define HOST_GENERATED_VALUE "
           << std::accumulate(values.begin(), values.end(), 0) << "\n";
    output.close();
    if (!output)
    {
        std::cerr << "Could not write the generated header.\n";
        return 1;
    }
    std::cout << "Native x86-64 C++ generator executed successfully.\n";
    return 0;
}
