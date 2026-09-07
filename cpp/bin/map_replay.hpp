// The shipped dialect map, replayed through the public registration API.
//
// Harness, not library. See cpp/bin/sqlreplay.cpp for what it proves.

#ifndef SEL_SQLT_MAP_REPLAY_HPP
#define SEL_SQLT_MAP_REPLAY_HPP

#include "../sel_sql_map.hpp"

namespace sel::sqlt {

using sel::sql::DialectSpec;
using sel::sql::EntrySpec;
using sel::sql::Map;
using sel::sql::Section;

// Registers every shipped dialect under a `~replay` suffix and returns the
// number of registration calls it took.
int replay_register();

}  // namespace sel::sqlt

#endif  // SEL_SQLT_MAP_REPLAY_HPP
