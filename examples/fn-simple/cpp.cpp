// Goes in the matching register_*() in cpp/sel.cpp. Not a runnable file: the
// C++ builtin table is fixed at startup and this fragment compiles only in place.
// EXAMPLE-BEGIN
define(Spec{"ORD_SUFFIX", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
              const long long n = a.non_neg_int(0);
              const long long tens = n % 100;
              if (tens >= 11 && tens <= 13) return make_text(std::to_string(n) + "th");
              switch (n % 10) {
                case 1: return make_text(std::to_string(n) + "st");
                case 2: return make_text(std::to_string(n) + "nd");
                case 3: return make_text(std::to_string(n) + "rd");
                default: return make_text(std::to_string(n) + "th");
              }
            }});
// EXAMPLE-END
