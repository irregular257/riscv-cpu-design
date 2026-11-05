#include "Vtb_coremark_perf.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
static uint64_t simulation_time=0;
double sc_time_stamp(){return static_cast<double>(simulation_time);}
int main(int argc,char**argv){
 if(argc!=3)return 2;
 Verilated::commandArgs(argc,argv);Vtb_coremark_perf d;
 std::ofstream uart(std::string(argv[1])+"/uart.log");
 const uint64_t limit=std::stoull(argv[2]);
 uint64_t cyc=0,begin=0,end=0,ret=0,iw=0,mw=0,ls=0,br=0,rd=0;
 uint32_t ih=0,im=0,ifl=0,dh=0,dm=0,dfl=0,dw=0;
 bool active=false,done=false;
 for(;cyc<limit && !done;++cyc){
  simulation_time=cyc*2;d.rst=cyc<8;d.clk=0;d.eval();
  if(!d.rst){
   if(d.trap){std::cerr<<"Unexpected trap at cycle "<<cyc<<" pc="<<std::hex<<d.debug_pc<<"\n";return 3;}
   if(d.uart_write){char c=char(d.write_data);uart.put(c);std::cout.put(c);}
   if(d.marker_write && d.write_data==0xc001){
    if(begin){std::cerr<<"Multiple benchmark starts\n";return 4;}
    begin=cyc;active=true;ih=d.ihit;im=d.imiss;ifl=d.ifill;dh=d.dhit;dm=d.dmiss;dfl=d.dfill;dw=d.dwrites;
   }else if(d.marker_write && d.write_data==0xc002){
    if(!active){std::cerr<<"Unexpected stop marker\n";return 4;}
    end=cyc;active=false;ih=d.ihit-ih;im=d.imiss-im;ifl=d.ifill-ifl;dh=d.dhit-dh;dm=d.dmiss-dm;dfl=d.dfill-dfl;dw=d.dwrites-dw;
   }else if(d.marker_write && d.write_data==0xc0decafe){done=true;}
   if(active){ret+=d.commit;iw+=d.if_wait;mw+=d.mem_wait;ls+=d.load_stall;br+=d.branch;rd+=d.redirect;}
  }
  ++simulation_time;d.clk=1;d.eval();
 }
 d.final();uart.close();
 if(!done||!begin||!end){std::cerr<<"Timeout or missing benchmark marker pc="<<std::hex<<d.debug_pc<<"\n";return 5;}
 std::ofstream o(std::string(argv[1])+"/metrics.json");
 o<<"{\n\"total_sim_cycles\":"<<cyc<<",\n\"window_cycles\":"<<(end-begin)<<",\n\"retired\":"<<ret
 <<",\n\"if_wait\":"<<iw<<",\n\"mem_wait\":"<<mw<<",\n\"load_use_bubbles\":"<<ls
 <<",\n\"branches\":"<<br<<",\n\"branch_redirects\":"<<rd
 <<",\n\"i_hits\":"<<ih<<",\n\"i_misses\":"<<im<<",\n\"i_fill_words\":"<<ifl
 <<",\n\"d_hits\":"<<dh<<",\n\"d_misses\":"<<dm<<",\n\"d_fill_words\":"<<dfl<<",\n\"backend_writes\":"<<dw<<"\n}\n";
 return 0;
}
