#include "Vvision_soc.h"
#include "verilated.h"
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
namespace fs=std::filesystem;
static uint64_t simulation_time_ps=0;
double sc_time_stamp(){return static_cast<double>(simulation_time_ps);}
static void require(bool value,const std::string& text){if(!value)throw std::runtime_error(text);}
static std::vector<uint8_t> read_bytes(const fs::path& path){
    std::ifstream f(path,std::ios::binary);require(bool(f),"Cannot read "+path.string());
    return std::vector<uint8_t>(std::istreambuf_iterator<char>(f),{});
}
static uint16_t u16(const std::vector<uint8_t>& b,size_t n){return uint16_t(b.at(n))|(uint16_t(b.at(n+1))<<8);}
static uint32_t u32(const std::vector<uint8_t>& b,size_t n){return uint32_t(u16(b,n))|(uint32_t(u16(b,n+2))<<16);}
static uint16_t crc16(const std::vector<uint8_t>& b){
    uint16_t crc=0xffff;
    for(size_t i=2;i+2<b.size();i++){
        crc^=uint16_t(b[i])<<8;
        for(unsigned k=0;k<8;k++)crc=uint16_t((crc&0x8000)?(crc<<1)^0x1021:(crc<<1));
    }return crc;
}
class Simulation{
public:
    std::unique_ptr<VerilatedContext> context=std::make_unique<VerilatedContext>();
    std::unique_ptr<Vvision_soc> top;
    uint64_t cycles=0,max_cycles,sample_cycle=0;
    unsigned divisor,bit_index=0;
    bool previous_tx=true,receiving=false,boot=true;
    uint8_t byte=0;
    std::string boot_text,stage="boot";
    std::vector<uint8_t> response;
    Simulation(uint64_t limit,unsigned div):max_cycles(limit),divisor(div){
        top=std::make_unique<Vvision_soc>(context.get());top->clk=0;top->rst=1;top->rx=1;top->ddr_enable=0;top->ddr_corrupt_reads=0;top->eval();
    }
    ~Simulation(){top->final();}
    void tick(){
        require(++cycles<=max_cycles,"Cycle limit in "+stage);
        top->clk=0;top->eval();context->timeInc(500000);simulation_time_ps+=500000;
        top->clk=1;top->eval();context->timeInc(500000);simulation_time_ps+=500000;
        if(top->halted || (top->test_result&0xffffff00u)){
            std::ostringstream s;s<<"SoC failed pc=0x"<<std::hex<<top->debug_pc<<" cause="<<top->trap_cause<<" test="<<top->test_result;
            throw std::runtime_error(s.str());
        }
        require(!top->rx_overrun,"UART input FIFO overrun");
        require(top->ddr_enable || !top->external_requests,"Vision/control unexpectedly accessed unavailable DDR");
        bool tx=top->tx;
        if(!receiving && previous_tx && !tx){receiving=true;bit_index=0;byte=0;sample_cycle=cycles+divisor+divisor/2;}
        if(receiving && cycles==sample_cycle){
            if(bit_index<8){if(tx)byte|=uint8_t(1u<<bit_index);bit_index++;sample_cycle+=divisor;}
            else{
                require(tx,"UART output stop bit framing error");receiving=false;
                if(boot){boot_text.push_back(char(byte));require(boot_text.find("FAIL")==std::string::npos,"Firmware boot FAIL");}
                else response.push_back(byte);
            }
        }
        previous_tx=tx;
        if(cycles%10000000==0)std::cout<<"PROGRESS cycles="<<cycles<<" stage="<<stage<<" dots="<<top->dot_starts<<std::endl;
    }
    void ticks(unsigned n){while(n--)tick();}
    void send(uint8_t b){
        top->rx=0;ticks(divisor);
        for(unsigned bit=0;bit<8;bit++){top->rx=(b>>bit)&1;ticks(divisor);}
        top->rx=1;ticks(divisor*3);
    }
    void receive(){
        size_t length=0;
        while(!length || response.size()<length){
            tick();
            if(response.size()>=2)require(response[0]==0x5a && (response[1]==0x43 || response[1]==0x59),"Bad reply prefix");
            if(response.size()>=9 && response[1]==0x43)length=11+u16(response,7);
            if(response.size()>=12 && response[1]==0x59){
                require(response[6]<=4 && response[7]<=1,"Bad vision count/flags");
                length=14+8*response[6]+((response[7]&1)?480:0);
            }
        }
        require(response.size()==length,"Unexpected response trailing bytes");
        require(crc16(response)==u16(response,response.size()-2),"Response CRC16 mismatch");
    }
};
int main(int argc,char**argv){
    try{
        require(argc==5,"usage: multimodal_runner plan.tsv output_dir max_cycles uart_divisor");
        fs::path plan=argv[1],out=argv[2];fs::create_directories(out);
        std::ifstream instructions(plan);require(bool(instructions),"Cannot read plan");
        auto started=std::chrono::steady_clock::now();
        Simulation sim(std::stoull(argv[3]),unsigned(std::stoul(argv[4])));sim.ticks(8);sim.top->rst=0;
        while(sim.boot_text.find("TINY GRID YOLO READY\r\n")==std::string::npos){sim.tick();require(sim.cycles<5000000,"Boot timeout");}
        require(sim.top->test_result==1,"Runtime monitor selftest incomplete");
        std::ofstream(out/"boot_uart.txt",std::ios::binary)<<sim.boot_text;
        sim.ticks(300);sim.boot=false;
        std::string line;std::vector<std::string> results;
        while(std::getline(instructions,line)){
            if(line.empty() || line[0]=='#')continue;
            std::istringstream entry(line);std::vector<std::string> f;std::string part;
            while(std::getline(entry,part,'\t'))f.push_back(part);
            require(f.size()>=8 && f.size()<=10,"Bad plan field count");
            std::string name=f[0],kind=f[1];fs::path here=plan.parent_path();
            auto request=read_bytes(here/f[2]);int glyph=std::stoi(f[5]),status=std::stoi(f[6]);
            unsigned dots=unsigned(std::stoul(f[7])),before_dots=sim.top->dot_starts;
            sim.top->ddr_enable=f.size()>=9 && f[8]=="1";
            sim.top->ddr_corrupt_reads=f.size()>=10 && f[9]=="1";
            unsigned before_external=sim.top->external_requests;
            sim.response.clear();sim.stage=name;uint64_t before=sim.cycles;
            for(auto b:request)sim.send(b);
            sim.receive();auto b=sim.response;
            require(b[2]==1,"Reply version mismatch");
            size_t heads_checked=0,boxes_checked=0;
            if(kind=="V"){
                require(b[1]==0x59 && b[3]==status && u16(b,4)==u16(request,6),"Vision status/sequence mismatch in "+name);
                if(status==0){
                    auto boxes=read_bytes(here/f[4]);auto head=read_bytes(here/f[3]);
                    require(size_t(b[6])*6==boxes.size(),"Vision box count mismatch");
                    require(b[7]==request[5] && u32(b,8)>0,"Vision flags/cycles mismatch");
                    for(size_t i=0;i<b[6];i++){
                        for(size_t k=0;k<6;k++)require(b[12+8*i+k]==boxes[6*i+k],"Vision box mismatch in "+name);
                        require(!b[18+8*i] && !b[19+8*i],"Box reserved bytes nonzero");
                    }
                    boxes_checked=b[6];size_t raw=12+8*b[6];
                    if(b[7]&1){require(head.size()==480,"Golden head size");for(size_t i=0;i<480;i++)require(b[raw+i]==head[i],"Raw head mismatch in "+name+" at byte "+std::to_string(i));heads_checked=240;}
                }else require(b[6]==0 && b[7]==0 && !u32(b,8),"Rejected vision executed inference");
            }else require(b[1]==0x43 && b[3]==request[3] && u16(b,4)==u16(request,4) && b[6]==status,"Control status/sequence mismatch in "+name);
            require(sim.top->dot_starts-before_dots==dots,"Unexpected accelerator starts in "+name);
            // One complete 16-column scan is 16016 cycles in this simulation.
            sim.ticks(20000);require(sim.response==b,"Unexpected extra reply bytes");
            if(glyph>=0){require(sim.top->cfg_glyph==glyph,"Firmware display MMIO glyph mismatch in "+name);require(sim.top->active_glyph==glyph,"Display frame did not apply glyph in "+name);}
            require(sim.top->display_enabled,"Firmware did not enable matrix peripheral");
            fs::path case_out=out/name;fs::create_directories(case_out);
            std::ofstream binary(case_out/"response.bin",std::ios::binary);binary.write(reinterpret_cast<const char*>(b.data()),b.size());binary.close();
            std::ostringstream metric;metric<<"{\"name\":\""<<name<<"\",\"result\":\"PASS\",\"request_bytes\":"<<request.size()<<",\"response_bytes\":"<<b.size()
                <<",\"status\":"<<status<<",\"cfg_glyph\":"<<unsigned(sim.top->cfg_glyph)<<",\"active_glyph\":"<<unsigned(sim.top->active_glyph)
                <<",\"brightness\":"<<unsigned(sim.top->active_brightness)<<",\"accelerator_starts\":"<<dots<<",\"head_values_checked\":"<<heads_checked
                <<",\"boxes_checked\":"<<boxes_checked<<",\"cycles\":"<<sim.cycles-before<<",\"external_requests\":"<<sim.top->external_requests
                <<",\"transaction_external_requests\":"<<sim.top->external_requests-before_external
                <<",\"behavioral_ddr_enabled\":"<<unsigned(sim.top->ddr_enable)<<",\"corrupted_ddr_reads\":"<<unsigned(sim.top->ddr_corrupt_reads)<<"}";
            results.push_back(metric.str());std::ofstream(case_out/"metrics.json")<<metric.str()<<'\n';
            std::cout<<"PASS "<<name<<" glyph="<<glyph<<" dots="<<dots<<" head="<<heads_checked<<std::endl;
        }
        std::ofstream summary(out/"runner_metrics.json");summary<<"{\"result\":\"PASS\",\"simulation_cycles\":"<<sim.cycles<<",\"retired\":"<<sim.top->retired
            <<",\"accelerator_starts\":"<<sim.top->dot_starts<<",\"external_requests\":"<<sim.top->external_requests<<",\"wall_seconds\":"
            <<std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count()<<",\"cases\":[";
        for(size_t i=0;i<results.size();i++){if(i)summary<<',';summary<<results[i];}summary<<"]}\n";
        std::cout<<"MULTIMODAL_SOC_PASS cases="<<results.size()<<" cycles="<<sim.cycles<<std::endl;
        return 0;
    }catch(const std::exception& e){std::cerr<<"MULTIMODAL_SOC_FAIL "<<e.what()<<std::endl;return 1;}
}
