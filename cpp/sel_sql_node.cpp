// See sel_sql_node.hpp.

#include "sel_sql_node.hpp"

namespace sel::sql {
namespace {

SNode::T from_nt(NT t) {
  // The two enums are declared in the same order, so this is a cast with a
  // switch around it to keep that from being an assumption.
  switch (t) {
    case NT::Num: return SNode::T::Num;
    case NT::Text: return SNode::T::Text;
    case NT::Bool: return SNode::T::Bool;
    case NT::Var: return SNode::T::Var;
    case NT::Index: return SNode::T::Index;
    case NT::Seq: return SNode::T::Seq;
    case NT::List: return SNode::T::List;
    case NT::Un: return SNode::T::Un;
    case NT::Bin: return SNode::T::Bin;
    case NT::Assign: return SNode::T::Assign;
    case NT::Call: return SNode::T::Call;
  }
  return SNode::T::Num;
}

const std::string EMPTY;

}  // namespace

SNodePtr SNode::leaf(NodePtr n) {
  auto out = std::shared_ptr<SNode>(new SNode);
  out->t_ = from_nt(n->t);
  out->pos_ = n->pos;
  out->origin_ = std::move(n);
  return out;
}

SNodePtr SNode::rewritten(NodePtr shape, std::vector<SNodePtr> kids) {
  auto out = std::shared_ptr<SNode>(new SNode);
  out->t_ = from_nt(shape->t);
  out->pos_ = shape->pos;
  out->origin_ = std::move(shape);
  out->kids_ = std::move(kids);
  return out;
}

std::shared_ptr<SNode> SNode::new_clist(Pos pos) {
  auto out = std::shared_ptr<SNode>(new SNode);
  out->t_ = T::CList;
  out->pos_ = pos;
  return out;
}

void SNode::append(std::string key, SNodePtr value) {
  keys_.push_back(std::move(key));
  kids_.push_back(std::move(value));
}

SNodePtr SNode::clist(Pos pos, std::vector<std::pair<std::string, SNodePtr>> entries) {
  auto out = std::shared_ptr<SNode>(new SNode);
  out->t_ = T::CList;
  out->pos_ = pos;
  out->keys_.reserve(entries.size());
  out->kids_.reserve(entries.size());
  for (auto& [k, v] : entries) {
    out->keys_.push_back(std::move(k));
    out->kids_.push_back(std::move(v));
  }
  return out;
}

const std::string& SNode::s() const { return origin_ ? origin_->s : EMPTY; }
bool SNode::b() const { return origin_ && origin_->b; }
bool SNode::grouped() const { return origin_ && origin_->grouped; }
const Spec* SNode::spec() const { return origin_ ? origin_->spec : nullptr; }

NodePtr SNode::to_node() const {
  if (t_ == T::CList) return nullptr;
  if (kids_.empty()) return origin_;   // a leaf: nothing was rewritten

  auto copy = std::make_shared<Node>(*origin_);
  copy->l.reset();
  copy->r.reset();
  copy->items.clear();

  const auto put = [](NodePtr& slot, const SNodePtr& kid) {
    slot = kid->to_node();
    return slot != nullptr;
  };
  switch (t_) {
    case T::Un:
      if (!put(copy->l, kids_[0])) return nullptr;
      break;
    case T::Bin:
    case T::Index:
    case T::Assign:
      if (!put(copy->l, kids_[0]) || !put(copy->r, kids_[1])) return nullptr;
      break;
    default:
      for (const SNodePtr& kid : kids_) {
        NodePtr child = kid->to_node();
        if (!child) return nullptr;
        copy->items.push_back(std::move(child));
      }
      break;
  }
  return copy;
}

std::string_view node_kind_name(SNode::T t) {
  switch (t) {
    case SNode::T::Num: return "num";
    case SNode::T::Text: return "text";
    case SNode::T::Bool: return "bool";
    case SNode::T::Var: return "var";
    case SNode::T::Index: return "index";
    case SNode::T::Seq: return "seq";
    case SNode::T::List: return "list";
    case SNode::T::Un: return "un";
    case SNode::T::Bin: return "bin";
    case SNode::T::Assign: return "assign";
    case SNode::T::Call: return "call";
    case SNode::T::CList: return "clist";
  }
  return "";
}

}  // namespace sel::sql
