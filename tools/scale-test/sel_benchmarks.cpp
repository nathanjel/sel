// C++ scale/parity runner.  It deliberately uses the checked-in Lisp fixture
// (benchmark_results.json) as the oracle for rows, SQL, and hybrid shape.

#include "../../cpp/sel.cpp"
#include "../../cpp/sel_sql.hpp"

#include <chrono>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <set>
#include <sstream>
#include <string_view>
#include <thread>
#include <vector>

#if defined(__unix__) || defined(__APPLE__)
#include <sys/utsname.h>
#include <unistd.h>
#endif

namespace {

using sel::Value;
using sel::sql::Binding;
using sel::sql::Bindings;
using sel::sql::HybridPlan;
using sel::sql::Sql;
using sel::sql::SqlKind;

std::string upper_ascii(std::string value) {
  for (char& ch : value) {
    if (ch >= 'a' && ch <= 'z') ch = static_cast<char>(ch - 'a' + 'A');
  }
  return value;
}

class Json {
 public:
  explicit Json(std::string text) : text_(std::move(text)) {}

  Value parse() {
    Value out = value();
    space();
    if (at_ != text_.size()) error("trailing JSON");
    return out;
  }

 private:
  std::string text_;
  std::size_t at_ = 0;

  [[noreturn]] void error(const char* message) const {
    throw std::runtime_error(std::string("JSON: ") + message);
  }

  void space() {
    while (at_ < text_.size() && (text_[at_] == ' ' || text_[at_] == '\n' ||
                                  text_[at_] == '\r' || text_[at_] == '\t')) {
      ++at_;
    }
  }

  void expect(char ch) {
    space();
    if (at_ >= text_.size() || text_[at_] != ch) error("unexpected character");
    ++at_;
  }

  static void append_utf8(std::string& out, unsigned cp) {
    if (cp <= 0x7f) out.push_back(static_cast<char>(cp));
    else if (cp <= 0x7ff) {
      out.push_back(static_cast<char>(0xc0 | (cp >> 6)));
      out.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
    } else if (cp <= 0xffff) {
      out.push_back(static_cast<char>(0xe0 | (cp >> 12)));
      out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3f)));
      out.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
    } else {
      out.push_back(static_cast<char>(0xf0 | (cp >> 18)));
      out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3f)));
      out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3f)));
      out.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
    }
  }

  std::string string_value() {
    expect('"');
    std::string out;
    while (at_ < text_.size()) {
      const char ch = text_[at_++];
      if (ch == '"') return out;
      if (ch != '\\') {
        out.push_back(ch);
        continue;
      }
      if (at_ >= text_.size()) error("unterminated string");
      const char esc = text_[at_++];
      switch (esc) {
        case '"': out.push_back('"'); break;
        case '\\': out.push_back('\\'); break;
        case '/': out.push_back('/'); break;
        case 'b': out.push_back('\b'); break;
        case 'f': out.push_back('\f'); break;
        case 'n': out.push_back('\n'); break;
        case 'r': out.push_back('\r'); break;
        case 't': out.push_back('\t'); break;
        case 'u': {
          if (at_ + 4 > text_.size()) error("bad unicode escape");
          unsigned cp = 0;
          for (int i = 0; i < 4; ++i) {
            const char h = text_[at_++];
            cp = cp * 16 + (h >= '0' && h <= '9' ? h - '0'
                            : h >= 'a' && h <= 'f' ? h - 'a' + 10
                            : h >= 'A' && h <= 'F' ? h - 'A' + 10 : -1);
          }
          append_utf8(out, cp);
          break;
        }
        default: error("bad escape");
      }
    }
    error("unterminated string");
  }

  Value number() {
    space();
    const std::size_t begin = at_;
    while (at_ < text_.size() && std::string_view("-+0123456789.eE").find(text_[at_]) !=
                                      std::string_view::npos) {
      ++at_;
    }
    const std::string token = text_.substr(begin, at_ - begin);
    try {
      if (token.find_first_of(".eE") == std::string::npos) {
        return Value::integer(std::stoll(token));
      }
      return Value::num(token);
    } catch (...) {
      return Value::text(token);
    }
  }

  Value value() {
    space();
    if (at_ >= text_.size()) error("unexpected end");
    switch (text_[at_]) {
      case '"': return Value::text(string_value());
      case '{': {
        ++at_;
        std::vector<std::string> keys;
        std::vector<Value> values;
        space();
        if (at_ < text_.size() && text_[at_] == '}') { ++at_; return Value::none(); }
        while (true) {
          const std::string key = string_value();
          expect(':');
          keys.push_back(key);
          values.push_back(value());
          space();
          if (at_ < text_.size() && text_[at_] == '}') {
            ++at_;
            return Value::record(std::move(keys), std::move(values));
          }
          expect(',');
        }
      }
      case '[': {
        ++at_;
        std::vector<Value> values;
        space();
        if (at_ < text_.size() && text_[at_] == ']') { ++at_; return Value::list({}); }
        while (true) {
          values.push_back(value());
          space();
          if (at_ < text_.size() && text_[at_] == ']') { ++at_; return Value::list(std::move(values)); }
          expect(',');
        }
      }
      case 't':
        if (text_.compare(at_, 4, "true") == 0) { at_ += 4; return Value::boolean(true); }
        break;
      case 'f':
        if (text_.compare(at_, 5, "false") == 0) { at_ += 5; return Value::boolean(false); }
        break;
      case 'n':
        if (text_.compare(at_, 4, "null") == 0) { at_ += 4; return Value::none(); }
        break;
      default:
        if (text_[at_] == '-' || (text_[at_] >= '0' && text_[at_] <= '9')) return number();
        break;
    }
    error("bad value");
  }
};

Value read_json(const std::string& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot read " + path);
  std::ostringstream text;
  text << input.rdbuf();
  return Json(text.str()).parse();
}

long long whole(const std::string& text, long long fallback) {
  try {
    const std::size_t dot = text.find('.');
    return std::stoll(text.substr(0, dot));
  } catch (...) {
    return fallback;
  }
}

void register_benchmark_builtins() {
  static bool once = false;
  if (once) return;
  once = true;
  using namespace sel;
  define(Spec{"CUSTOM_VIP_SCORE", 2, 2, false, false, nullptr,
              [](Args& a, Context&) -> Value {
                const std::string tier = a.text(0);
                const long long year = whole(a.text(1), 2024);
                const long long base = tier == "PLATINUM" ? 100 : tier == "GOLD" ? 50
                                       : tier == "SILVER" ? 25 : 10;
                return Value::integer(base + (2026 - year) * 5);
              }});
  define(Spec{"HOST_RISK_SCORE", 2, 2, false, false, nullptr,
              [](Args& a, Context&) -> Value {
                return Value::integer((a.text(0) == "US" ? 30 : 10) +
                                      whole(a.text(1), 0) * 2);
              }});
}

Binding relation(const std::string& table,
                 const std::vector<std::pair<std::string, std::string>>& fields) {
  std::vector<std::pair<std::string, Binding>> mapped;
  for (const auto& [key, column] : fields) {
    const bool text = key == "CODE" || key == "NAME" || key == "SKU" ||
                      key == "COUNTRY" || key == "TIER" || key == "STATUS";
    mapped.emplace_back(key, Binding::column(column, table,
                                              text ? SqlKind::Text : SqlKind::Num));
  }
  return Binding::relation(table, table, std::move(mapped));
}

Bindings schema(const std::string& dialect) {
  const Binding categories = relation("categories", {
      {"ID", "id"}, {"CODE", "code"}, {"NAME", "name"}, {"VAT_RATE", "vat_rate"}});
  const Binding products = relation("products", {
      {"ID", "id"}, {"SKU", "sku"}, {"NAME", "name"}, {"CATEGORY_ID", "category_id"},
      {"PRICE", "price"}, {"IS_ACTIVE", "is_active"}});
  std::vector<std::pair<std::string, Binding>> customer_fields = {
      {"ID", Binding::column("id", "customers", SqlKind::Num)},
      {"NAME", Binding::column("name", "customers", SqlKind::Text)},
      {"TIER", Binding::column("tier", "customers", SqlKind::Text)},
      {"COUNTRY", Binding::column("country", "customers", SqlKind::Text)},
      {"CREATED_YEAR", Binding::column("created_year", "customers", SqlKind::Num)},
      {"LATITUDE", Binding::column("latitude", "customers", SqlKind::Num)},
      {"LONGITUDE", Binding::column("longitude", "customers", SqlKind::Num)},
      {"DIST_BERLIN", Binding::raw(
          dialect == "postgresql"
              ? "ROUND((customers.location <-> point(13.404954, 52.520008))::numeric, 6)"
              : "ROUND(ST_Distance(POINT(customers.longitude, customers.latitude), "
                "POINT(13.404954, 52.520008)), 6)", SqlKind::Num)}};
  const Binding customers = Binding::relation("customers", "customers", std::move(customer_fields));
  const Binding orders = relation("orders", {
      {"ID", "id"}, {"CUSTOMER_ID", "customer_id"}, {"STATUS", "status"},
      {"DISCOUNT", "discount"}, {"ORDER_YEAR", "order_year"}});
  const Binding items = relation("order_items", {
      {"ID", "id"}, {"ORDER_ID", "order_id"}, {"PRODUCT_ID", "product_id"},
      {"QUANTITY", "quantity"}, {"UNIT_PRICE", "unit_price"}});
  return Bindings({{"CATEGORIES", categories}, {"PRODUCTS", products},
                   {"CUSTOMERS", customers}, {"ORDERS", orders}, {"ORDER_ITEMS", items}});
}

void add_distances(Value& context) {
  Value* customers = context.get("CUSTOMERS");
  if (!customers) return;
  std::vector<Value> shaped_customers;
  shaped_customers.reserve(customers->size());
  const auto add = [&shaped_customers](const Value& customer) {
    const double latitude = std::stof(customer.get("latitude")->as_text());
    const double longitude = std::stof(customer.get("longitude")->as_text());
    const double dx = longitude - 13.404954;
    const double dy = latitude - 52.520008;
    std::ostringstream distance;
    distance << std::fixed << std::setprecision(6) << std::sqrt(dx * dx + dy * dy);
    std::vector<std::string> keys = customer.keys();
    std::vector<Value> values;
    values.reserve(keys.size() + 1);
    if (customer.shape() && customer.storage().size() == keys.size()) {
      // Fixture rows are shaped: copy the flat handles directly rather than
      // allocating one key/value pair per field merely to rebuild the row.
      values = customer.storage();
    } else {
      for (const auto& [key, value] : customer.entries()) {
        (void)key;
        values.push_back(value);
      }
    }
    keys.push_back("dist_berlin");
    values.push_back(Value::num(distance.str()));
    shaped_customers.push_back(Value::record(std::move(keys), std::move(values)));
  };
  if (customers->is_list()) {
    for (std::size_t i = 0; i < customers->size(); ++i) {
      if (const Value* source_customer = customers->slot(i)) add(*source_customer);
    }
  } else {
    for (const auto& [ignored, source_customer] : customers->entries()) {
      (void)ignored;
      add(source_customer);
    }
  }
  context.set("CUSTOMERS", Value::list(std::move(shaped_customers)));
}

std::string proc_field(const std::string& prefix) {
  std::ifstream input("/proc/cpuinfo");
  if (!input) return "unknown";
  std::string line;
  while (std::getline(input, line)) {
    if (line.rfind(prefix, 0) != 0) continue;
    const std::size_t colon = line.find(':');
    if (colon == std::string::npos) continue;
    const std::size_t begin = line.find_first_not_of(" \t", colon + 1);
    return begin == std::string::npos ? "unknown" : line.substr(begin);
  }
  return "unknown";
}

std::uint64_t available_memory_bytes() {
#if defined(_SC_AVPHYS_PAGES) && defined(_SC_PAGE_SIZE)
  const long pages = sysconf(_SC_AVPHYS_PAGES);
  const long page_size = sysconf(_SC_PAGE_SIZE);
  if (pages > 0 && page_size > 0) {
    return static_cast<std::uint64_t>(pages) *
           static_cast<std::uint64_t>(page_size);
  }
#endif
  return 0;
}

std::string host_os() {
#if defined(__unix__) || defined(__APPLE__)
  struct utsname info {};
  if (uname(&info) == 0) {
    return std::string(info.sysname) + " " + info.release;
  }
#endif
  return "unknown";
}

std::string host_machine() {
#if defined(__unix__) || defined(__APPLE__)
  struct utsname info {};
  if (uname(&info) == 0) return info.machine;
#endif
  return "unknown";
}

std::string arg(int argc, char** argv, std::string_view name, std::string fallback) {
  for (int i = 1; i + 1 < argc; ++i) {
    if (argv[i] == name) return argv[i + 1];
  }
  return fallback;
}

int integer_arg(int argc, char** argv, std::string_view name, int fallback) {
  try {
    return std::stoi(arg(argc, argv, name, std::to_string(fallback)));
  } catch (...) {
    return fallback;
  }
}

bool selected_id(const std::string& only, const std::string& id) {
  if (only.empty()) return true;
  std::size_t begin = 0;
  while (begin <= only.size()) {
    const std::size_t end = only.find(',', begin);
    if (only.substr(begin, end == std::string::npos ? std::string::npos : end - begin) == id) {
      return true;
    }
    if (end == std::string::npos) break;
    begin = end + 1;
  }
  return false;
}

struct RepresentationCounts {
  std::size_t shaped_records = 0;
  std::size_t fallback_records = 0;
  std::size_t lists = 0;
};

void count_representation(const Value& value, RepresentationCounts& counts) {
  if (value.is_list()) {
    ++counts.lists;
  } else if (value.shape()) {
    ++counts.shaped_records;
  } else if (value.size() > 0) {
    ++counts.fallback_records;
  }
  if (value.shape()) {
    for (const Value& child : value.storage()) count_representation(child, counts);
  } else if (value.is_list() && !value.storage().empty()) {
    for (const Value& child : value.storage()) count_representation(child, counts);
  } else {
    for (const auto& [key, child] : value.entries()) {
      (void)key;
      count_representation(child, counts);
    }
  }
}

std::string json_escape(std::string_view text) {
  std::string out;
  out.reserve(text.size() + 2);
  out.push_back('"');
  for (unsigned char ch : text) {
    switch (ch) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (ch < 0x20) {
          std::ostringstream escaped;
          escaped << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                  << static_cast<unsigned>(ch);
          out += escaped.str();
        } else {
          out.push_back(static_cast<char>(ch));
        }
    }
  }
  out.push_back('"');
  return out;
}

std::string shell_quote(std::string_view text) {
  std::string out{"'"};
  for (const char ch : text) {
    if (ch == '\'') out += "'\\''";
    else out.push_back(ch);
  }
  out.push_back('\'');
  return out;
}

std::string file_sha256(const std::string& path) {
  const std::string command = "sha256sum " + shell_quote(path);
  FILE* pipe = popen(command.c_str(), "r");
  if (!pipe) return {};
  char buffer[256]{};
  const bool read = std::fgets(buffer, sizeof(buffer), pipe) != nullptr;
  const int status = pclose(pipe);
  if (!read || status != 0) return {};
  const std::string line(buffer);
  const std::size_t end = line.find_first_of(" \t\r\n");
  return line.substr(0, end == std::string::npos ? line.size() : end);
}

struct Sample {
  double context_clone_ms = 0.0;
  double run_ms = 0.0;
  double materialize_ms = 0.0;
  double prepared_ms = 0.0;
};

struct ScenarioRun {
  std::string id;
  Value expected;
  sel::Program program = sel::compile("NULL");
  double compile_ms = 0.0;
  std::vector<std::string> failures;
  std::vector<Sample> samples;
  std::size_t rows = 0;
};

void write_report(const std::string& output, const std::string& dataset_path,
                  const std::string& reference_path, const std::string& timing_mode,
                  const std::string& context_mode,
                  int runs, int warmups, const RepresentationCounts& counts,
                  const Value& dataset, const std::string& binary_path,
                  const std::vector<ScenarioRun>& cases, bool passed) {
  if (output.empty()) return;
  std::ofstream out(output);
  if (!out) throw std::runtime_error("cannot write " + output);
  const char* configured_flags = std::getenv("CXXFLAGS");
  const char* compiler_driver = std::getenv("CXX");
  const unsigned logical_cpus = std::thread::hardware_concurrency();
  std::size_t total_source_rows = 0;
  std::ostringstream table_rows;
  std::ostringstream reference_ids;
  bool first_table = true;
  for (const auto& [table, rows] : dataset.entries()) {
    if (!first_table) table_rows << ", ";
    first_table = false;
    table_rows << json_escape(upper_ascii(table)) << ": " << rows.size();
    total_source_rows += rows.size();
  }
  for (std::size_t i = 0; i < cases.size(); ++i) {
    if (i) reference_ids << ", ";
    reference_ids << json_escape(cases[i].id);
  }
  out << "{\n  \"schema_version\": 2,\n  \"implementation\": \"cpp\",\n"
      << "  \"passed\": " << (passed ? "true" : "false") << ",\n"
      << "  \"metadata\": {\n"
      << "    \"fixture\": {\"path\": " << json_escape(dataset_path)
      << ", \"sha256\": " << json_escape(file_sha256(dataset_path))
      << ", \"table_rows\": {" << table_rows.str() << "}, \"total_source_rows\": "
      << total_source_rows << ", \"schema_version\": 1},\n"
      << "    \"reference_path\": " << json_escape(reference_path) << ",\n"
      << "    \"reference_sha256\": " << json_escape(file_sha256(reference_path)) << ",\n"
      << "    \"reference_scenario_ids\": [" << reference_ids.str() << "],\n"
      << "    \"runtime\": {\"os\": " << json_escape(host_os())
      << ", \"machine\": " << json_escape(host_machine())
      << ", \"cpu\": " << json_escape(proc_field("model name"))
      << ", \"logical_cpus\": " << logical_cpus
      << ", \"available_memory_bytes\": " << available_memory_bytes()
      << ", \"compiler_driver\": " << json_escape(compiler_driver ? compiler_driver : "c++")
      << ", \"compiler_version\": " << json_escape(__VERSION__)
      << ", \"cxxflags\": " << json_escape(configured_flags ? configured_flags
                                                        : "-std=c++23 -O2 -Wall -Wextra -Wpedantic")
      << ", \"binary_path\": " << json_escape(binary_path)
      << ", \"binary_sha256\": " << json_escape(file_sha256(binary_path)) << "},\n"
      << "    \"representation\": {\"shaped_records\": " << counts.shaped_records
      << ", \"fallback_records\": " << counts.fallback_records
      << ", \"lists\": " << counts.lists << "},\n"
      << "    \"timing_mode\": " << json_escape(timing_mode)
      << ", \"context_mode\": " << json_escape(context_mode)
      << ", \"gc_policy\": \"not applicable (native C++)\""
      << ", \"runs\": " << runs << ", \"warmups\": " << warmups << "\n"
      << "  },\n  \"scenarios\": [\n";
  for (std::size_t i = 0; i < cases.size(); ++i) {
    const ScenarioRun& item = cases[i];
    const bool context_unchanged = std::none_of(
        item.failures.begin(), item.failures.end(), [](const std::string& failure) {
          return failure.find("context") != std::string::npos;
        });
    out << "    {\n      \"id\": " << json_escape(item.id)
        << ",\n      \"rows\": " << item.rows
        << ",\n      \"compile_ms\": " << std::setprecision(12) << item.compile_ms
        << ",\n      \"samples\": [\n";
    for (std::size_t j = 0; j < item.samples.size(); ++j) {
      const Sample& sample = item.samples[j];
      out << "        {\"context_clone_ms\": " << sample.context_clone_ms
          << ", \"program_run_ms\": " << sample.run_ms
          << ", \"materialize_ms\": " << sample.materialize_ms
          << ", \"prepared_total_ms\": " << sample.prepared_ms
          << ", \"elapsed_ms\": " << sample.prepared_ms << "}"
          << (j + 1 == item.samples.size() ? "\n" : ",\n");
    }
    out << "      ],\n      \"context_unchanged\": "
        << (context_unchanged ? "true" : "false")
        << ",\n      \"passed\": " << (item.failures.empty() ? "true" : "false")
        << ",\n      \"failures\": [";
    for (std::size_t j = 0; j < item.failures.size(); ++j) {
      if (j) out << ", ";
      out << json_escape(item.failures[j]);
    }
    out << "],\n      \"parity\": {\"passed\": "
        << (item.failures.empty() ? "true" : "false")
        << ", \"failures\": [";
    for (std::size_t j = 0; j < item.failures.size(); ++j) {
      if (j) out << ", ";
      out << json_escape(item.failures[j]);
    }
    out << "]}\n    }" << (i + 1 == cases.size() ? "\n" : ",\n");
  }
  out << "  ]\n}\n";
}

}  // namespace

int main(int argc, char** argv) {
  try {
    register_benchmark_builtins();
    const std::string dataset_path = arg(argc, argv, "--dataset", "tools/scale-test/dataset-10x.json");
    const std::string reference_path = arg(argc, argv, "--reference", "tools/scale-test/benchmark_results.json");
    const std::string output_path = arg(argc, argv, "--output", "");
    const std::string only = arg(argc, argv, "--only", "");
    const std::string timing_mode = arg(argc, argv, "--timing-mode", "steady-state");
    const std::string context_mode = arg(argc, argv, "--context-mode", "isolated");
    const int runs = integer_arg(argc, argv, "--runs", 1);
    const int warmups = integer_arg(argc, argv, "--warmups", 0);
    if (runs < 1 || warmups < 0) throw std::runtime_error("--runs must be positive and --warmups non-negative");
    if (timing_mode != "steady-state" && timing_mode != "gc-controlled") {
      throw std::runtime_error("unsupported timing mode: " + timing_mode);
    }
    if (context_mode != "isolated" && context_mode != "reuse") {
      throw std::runtime_error("unsupported context mode: " + context_mode);
    }

    const Value dataset = read_json(dataset_path);
    const Value reference = read_json(reference_path);
    Value context = Value::none();
    for (const auto& [table, rows] : dataset.entries()) context.set(upper_ascii(table), rows);
    add_distances(context);
    RepresentationCounts counts;
    count_representation(context, counts);
    const std::uint64_t context_signature = context.structural_hash();

    std::vector<ScenarioRun> cases;
    for (const auto& [index, expected] : reference.entries()) {
      (void)index;
      const Value* id_value = expected.get("id");
      if (!id_value) throw std::runtime_error("reference scenario has no id");
      const std::string id = id_value->as_text();
      if (!selected_id(only, id)) continue;
      const Value* query_value = expected.get("query");
      if (!query_value) throw std::runtime_error("reference scenario has no query: " + id);
      const auto compile_start = std::chrono::steady_clock::now();
      ScenarioRun item{id, expected, sel::compile(query_value->as_text()), 0.0, {}, {}, 0};
      const auto compile_stop = std::chrono::steady_clock::now();
      item.compile_ms = std::chrono::duration<double, std::milli>(compile_stop - compile_start).count();
      const bool expected_hybrid = [&] {
        const Value* value = expected.get("is_hybrid");
        return value && !value->is_null() && value->as_bool();
      }();
      const bool expected_continuation = [&] {
        const Value* value = expected.get("has_continuation");
        return value && !value->is_null() && value->as_bool();
      }();
      for (const std::string dialect : {"postgresql", "mariadb"}) {
        const HybridPlan plan = Sql::plan_hybrid(item.program, dialect, schema(dialect));
        const std::string key = dialect == "postgresql" ? "sql_postgres" : "sql_mariadb";
        const Value* expected_sql = expected.get(key);
        const bool expected_has_sql = expected_sql && !expected_sql->is_null();
        const bool actual_has_sql = plan.sql_statement.has_value();
        if (expected_has_sql != actual_has_sql) {
          item.failures.push_back(dialect + " SQL presence differs");
        } else if (expected_has_sql && plan.sql_statement->as_statement() != expected_sql->as_text()) {
          item.failures.push_back(dialect + " SQL differs from Lisp reference");
        }
        if (plan.is_hybrid != expected_hybrid) item.failures.push_back(dialect + " hybrid flag differs");
        if (plan.pure_sql != !expected_hybrid) item.failures.push_back(dialect + " pureSql flag differs");
        if (plan.continuation_program.has_value() != expected_continuation) {
          item.failures.push_back(dialect + " continuation presence differs");
        }
      }
      cases.push_back(std::move(item));
    }
    if (cases.empty()) throw std::runtime_error("--only selected no scenarios");

    const bool isolate_context = context_mode == "isolated";
    const auto execute = [isolate_context](const sel::Program& program,
                                            const Value& prepared_context) {
      Sample sample;
      const auto clone_start = std::chrono::steady_clock::now();
      Value input = isolate_context ? prepared_context.clone() : prepared_context;
      const auto clone_stop = std::chrono::steady_clock::now();
      sample.context_clone_ms =
          std::chrono::duration<double, std::milli>(clone_stop - clone_start).count();
      const auto run_start = clone_stop;
      Value actual = program.run(input);
      const auto run_stop = std::chrono::steady_clock::now();
      sample.run_ms = std::chrono::duration<double, std::milli>(run_stop - run_start).count();
      const auto materialize_start = run_stop;
      (void)actual.dump();
      const auto materialize_stop = std::chrono::steady_clock::now();
      sample.materialize_ms = std::chrono::duration<double, std::milli>(materialize_stop - materialize_start).count();
      // The common prepared_total_ms contract excludes context preparation;
      // context_clone_ms is reported independently above.
      sample.prepared_ms = std::chrono::duration<double, std::milli>(materialize_stop - run_start).count();
      return std::pair<Value, Sample>{std::move(actual), sample};
    };

    bool all_passed = true;
    for (ScenarioRun& item : cases) {
      const Value* expected_rows = item.expected.get("in_memory_rows");
      item.rows = expected_rows ? expected_rows->size() : 0;
      const std::uint64_t validation_before = context.structural_hash();
      try {
        auto validation = execute(item.program, context);
        if (!expected_rows || !validation.first.eql(*expected_rows)) {
          item.failures.push_back("in-memory rows differ from Lisp reference during validation");
        }
      } catch (const std::exception& error) {
        item.failures.push_back(std::string("validation runtime: ") + error.what());
      }
      if (validation_before != context.structural_hash() || context.structural_hash() != context_signature) {
        item.failures.push_back("prepared context changed during untimed validation");
      }

      for (int warmup = 0; warmup < warmups; ++warmup) {
        std::cout << "[cpp] " << item.id << " warmup " << (warmup + 1) << "/" << warmups << "\n";
        try {
          auto result = execute(item.program, context);
          if (!expected_rows || !result.first.eql(*expected_rows)) item.failures.push_back("warmup result differs from Lisp reference");
        } catch (const std::exception& error) {
          item.failures.push_back(std::string("warmup runtime: ") + error.what());
        }
      }
      for (int run = 0; run < runs; ++run) {
        std::cout << "[cpp] " << item.id << " measured " << (run + 1) << "/" << runs << "\n";
        try {
          auto result = execute(item.program, context);
          if (!expected_rows || !result.first.eql(*expected_rows)) item.failures.push_back("measured result differs from Lisp reference");
          if (context.structural_hash() != context_signature) item.failures.push_back("measured run changed prepared context");
          item.samples.push_back(result.second);
        } catch (const std::exception& error) {
          item.failures.push_back(std::string("measured runtime: ") + error.what());
        }
      }
      const bool passed = item.failures.empty() && item.samples.size() == static_cast<std::size_t>(runs);
      all_passed = all_passed && passed;
      std::cout << (passed ? "PASS " : "FAIL ") << item.id << ": "
                << (passed ? "in-memory + PostgreSQL SQL + MariaDB SQL" : item.failures.front()) << "\n";
    }
    write_report(output_path, dataset_path, reference_path, timing_mode, context_mode,
                 runs, warmups,
                 counts, dataset, argv[0], cases, all_passed);
    std::cout << "C++ scale parity: "
              << std::count_if(cases.begin(), cases.end(), [runs](const ScenarioRun& item) {
                   return item.failures.empty() && item.samples.size() == static_cast<std::size_t>(runs);
                 })
              << "/" << cases.size() << " passed; mode=" << timing_mode
              << "; context=" << context_mode
              << "; runs=" << runs << "; warmups=" << warmups << "\n";
    return all_passed ? 0 : 1;
  } catch (const std::exception& error) {
    std::cerr << "C++ scale runner: " << error.what() << "\n";
    return 1;
  }
}
