#include "sel.hpp"
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
using Clock=std::chrono::steady_clock;
int main(int argc,char**argv){
 if(argc!=2)return 2;
 std::ifstream input("examples/mandelbrot.sel");std::string source((std::istreambuf_iterator<char>(input)),{});
 auto t=Clock::now();auto p=sel::compile(source);double compile_ms=std::chrono::duration<double,std::milli>(Clock::now()-t).count();
 std::ofstream out(argv[1]);out<<"{\"compile_ms\":"<<compile_ms<<",\"samples_ms\":[";
 int warmups=std::getenv("MANDEL_WARMUPS")?std::atoi(std::getenv("MANDEL_WARMUPS")):2;
 int runs=std::getenv("MANDEL_RUNS")?std::atoi(std::getenv("MANDEL_RUNS")):5;
 std::string outputs;
 for(int i=0;i<warmups+runs;i++){t=Clock::now();auto result=p.run();auto output=result.as_text();double ms=std::chrono::duration<double,std::milli>(Clock::now()-t).count();if(i>=warmups){if(i>warmups){out<<',';outputs+=',';}out<<ms;outputs+=sel::Value::text(output).dump().substr(1);}}
 out<<"],\"outputs\":["<<outputs<<"],\"warmups\":"<<warmups<<",\"runs\":"<<runs<<"}";
}
