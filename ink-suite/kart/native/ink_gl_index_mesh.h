#ifndef INK_GL_INDEX_MESH_H
#define INK_GL_INDEX_MESH_H
// Lossless immutable-mesh indexing. Complete uploaded vertex bytes are the
// key: positions, seams, per-face colors, alpha and UV remain bit-identical.
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <unordered_map>
#include <vector>
namespace inkgl_indexing {
struct Key {std::array<uint32_t,9> words{};bool operator==(const Key&b)const{return words==b.words;}};
struct Hash {size_t operator()(const Key&k)const{size_t h=2166136261u;for(uint32_t w:k.words){h^=w;h*=16777619u;}return h;}};
struct Result {std::vector<unsigned char> vertices;std::vector<uint16_t> indices;bool enabled=false;int unique=0;};
inline Result build(const void*data,int count,size_t stride){
 Result out;if(!data||count<=0||(stride!=20&&stride!=36))return out;
 const auto*bytes=static_cast<const unsigned char*>(data);std::unordered_map<Key,uint16_t,Hash> lookup;
 lookup.reserve(size_t(count));out.vertices.reserve(size_t(count)*stride);out.indices.reserve(size_t(count));
 for(int i=0;i<count;i++){Key key;std::memcpy(key.words.data(),bytes+size_t(i)*stride,stride);auto found=lookup.find(key);
  if(found==lookup.end()){
   if(out.unique>=65535)return Result{};
   uint16_t index=static_cast<uint16_t>(out.unique++);lookup.emplace(key,index);out.indices.push_back(index);
   out.vertices.insert(out.vertices.end(),bytes+size_t(i)*stride,bytes+size_t(i+1)*stride);
  }else out.indices.push_back(found->second);
 }
 // Do not pay index fetch/storage for meshes with essentially no reuse.
 if(out.vertices.size()+out.indices.size()*2>=size_t(count)*stride)return Result{};
 out.enabled=true;return out;
}
}
#endif
