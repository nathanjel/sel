#include "../../cpp/sel_sql.hpp"
#include <fstream>
#include <iostream>
#include <cstdlib>
using namespace sel::sql;
std::string q(const std::string& s){return sel::Value::text(s).dump().substr(1);}
int main(){
 Bindings b({{"R",Binding::relation("r","r",{{"ID",Binding::column("id","r",SqlKind::Num)},{"CAT",Binding::column("cat","r",SqlKind::Text)},{"V",Binding::column("v","r",SqlKind::Num)},{"FK",Binding::column("fk","r",SqlKind::Num)}})},
 {"S",Binding::relation("s","s",{{"ID",Binding::column("id","s",SqlKind::Num)},{"CAT",Binding::column("cat","s",SqlKind::Text)},{"V",Binding::column("v","s",SqlKind::Num)},{"FK",Binding::column("fk","s",SqlKind::Num)}})}});
 std::ifstream in("tools/adversarial/queries.sel");std::string source;int i=0;
 std::ifstream replay;if(std::getenv("AUDIT_REPLAY"))replay.open("tools/adversarial/replay-cpp.sel");
 while(std::getline(in,source)){
  auto p=sel::compile(source);
  for(const std::string d:{"sqlite","postgresql","mariadb"})for(bool strict:{false,true}){
   std::cout<<"{\"i\":"<<i<<",\"d\":"<<q(d)<<",\"strict\":"<<(strict?"true":"false");
   try{auto f=Sql::translate_statement(p,d,b,Options{strict});auto sql=f.as_statement();auto ps=f.as_statement(Mode::Params);std::cout<<",\"sql\":"<<q(sql)<<",\"params_sql\":"<<q(ps)<<",\"params\":[";bool first=true;for(auto v:f.bindings()){if(!first)std::cout<<",";first=false;std::cout<<q(v.as_text());}std::cout<<"]";}
   catch(const SqlError& e){std::cout<<",\"error\":"<<q(e.code());}
   catch(const std::exception& e){std::cout<<",\"error\":"<<q(e.what());}
   try{auto h=Sql::plan_hybrid(p,d,b,Options{strict});std::cout<<",\"plan\":"<<q(h.pure_sql?"pure_sql":h.pure_memory?"pure_memory":"hybrid")<<",\"prefix\":"<<(h.sql_statement?q(h.sql_statement->as_statement()):"null");}
   catch(const SqlError& e){std::cout<<",\"plan_error\":"<<q(e.code());}
   if(replay.is_open()){std::string line;std::getline(replay,line);auto data=sel::compile(line).run();try{if(!data.get("error")->as_text().empty())throw std::runtime_error("DB_ERROR");auto h=Sql::plan_hybrid(p,d,b,Options{strict});auto v=Sql::execute_hybrid(h,[&](auto&,auto&){return *data.get("rows");},*data.get("context"));std::cout<<",\"hybrid_value\":"<<q(v.dump());}catch(const sel::SelError& e){std::cout<<",\"hybrid_error\":"<<q(e.code());}catch(const std::exception&e){std::cout<<",\"hybrid_error\":"<<q(e.what());}}
   std::cout<<"}\n";
  }++i;
 }
}
