// CASSound offline VST3 effect host. A disposable child process owns every
// plugin instance.
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivstprocesscontext.h"
#include "pluginterfaces/vst/vstspeaker.h"
#include "public.sdk/source/vst/hosting/hostclasses.h"
#include "public.sdk/source/vst/hosting/module.h"
#include "public.sdk/source/vst/hosting/parameterchanges.h"
#include "public.sdk/source/vst/hosting/plugprovider.h"
#include "public.sdk/source/vst/hosting/processdata.h"
#include "public.sdk/source/vst/utility/memoryibstream.h"
#include "public.sdk/source/vst/utility/stringconvert.h"
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <nlohmann/json.hpp>
#ifdef _WIN32
#include <objbase.h>
#endif
using namespace Steinberg;
using namespace Steinberg::Vst;
using json = nlohmann::json;
static void require(bool ok, const char *msg) {
  if (!ok)
    throw std::runtime_error(msg);
}
static std::string utf(const TChar *x) {
  return StringConvert::convert(
      std::u16string(reinterpret_cast<const char16_t *>(x)));
}
static std::vector<uint8> state(IComponent *c) {
  ResizableMemoryIBStream stream;
  require(c->getState(&stream) == kResultOk,
          "Plugin cannot save component state");
  auto data = stream.take();
  require(data.size() <= 4 * 1024 * 1024, "Plugin state exceeds 4 MiB");
  return data;
}
static void loadstate(IComponent *c, IEditController *ctrl, const json &j) {
  if (j.is_null())
    return;
  auto data = j.get<std::vector<uint8>>();
  require(data.size() <= 4 * 1024 * 1024, "State too large");
  ResizableMemoryIBStream stream;
  stream.write(data.data(), static_cast<int32>(data.size()), nullptr);
  stream.rewind();
  require(c->setState(&stream) == kResultOk, "Plugin rejected component state");
  stream.rewind();
  if (ctrl)
    require(ctrl->setComponentState(&stream) == kResultOk,
            "Controller rejected component state");
}
static json inspect(PlugProvider &provider) {
  auto c = provider.getComponentPtr();
  auto ctrl = provider.getControllerPtr();
  json params = json::array(), buses = json::array();
  if (ctrl) {
    require(ctrl->getParameterCount() <= 4096, "Too many plugin parameters");
    for (int32 i = 0; i < ctrl->getParameterCount(); ++i) {
      ParameterInfo p{};
      if (ctrl->getParameterInfo(i, p) != kResultOk)
        continue;
      params.push_back({{"id", p.id},
                        {"name", utf(p.title)},
                        {"units", utf(p.units)},
                        {"steps", p.stepCount},
                        {"value", ctrl->getParamNormalized(p.id)},
                        {"default", p.defaultNormalizedValue},
                        {"flags", p.flags}});
    }
  }
  for (auto dir : {kInput, kOutput})
    for (int32 i = 0; i < c->getBusCount(kAudio, dir); ++i) {
      BusInfo b{};
      require(c->getBusInfo(kAudio, dir, i, b) == kResultOk, "Bus info failed");
      buses.push_back({{"direction", dir == kInput ? "input" : "output"},
                       {"index", i},
                       {"name", utf(b.name)},
                       {"channels", b.channelCount},
                       {"main", b.busType == kMain}});
    }
  return {{"parameters", params}, {"buses", buses}};
}
static json run(const json &req) {
  std::string error;
  auto module =
      VST3::Hosting::Module::create(req.at("plugin").get<std::string>(), error);
  require(bool(module), error.c_str());
  HostApplication context;
  PluginContextFactory::instance().setPluginContext(&context);
  module->getFactory().setHostContext(&context);
  auto factory = module->getFactory();
  json classes = json::array();
  for (auto &info : factory.classInfos()) {
    if (info.category() != kVstAudioEffectClass ||
        info.subCategoriesString().find("Instrument") != std::string::npos)
      continue;
    std::string id = info.ID().toString();
    if (req.value("mode", std::string("scan")) == "scan") {
      auto provider = owned(new PlugProvider(factory, info, true));
      require(provider->initialize(), "Effect initialization failed");
      json item = inspect(*provider);
      item["class_id"] = id;
      item["name"] = info.name();
      item["categories"] = info.subCategoriesString();
      classes.push_back(item);
      continue;
    }
    if (req.value("class_id", std::string()) != id)
      continue;
    auto provider = owned(new PlugProvider(factory, info, true));
    require(provider->initialize(), "Effect initialization failed");
    auto component = provider->getComponentPtr();
    auto ctrl = provider->getControllerPtr();
    auto processor = U::cast<IAudioProcessor>(component);
    require(bool(processor), "No audio processor");
    require(processor->canProcessSampleSize(kSample32) == kResultTrue,
            "No float32 processing");
    int channels = req.at("channels");
    int frames = req.at("frames");
    double rate = req.at("sample_rate");
    require((channels == 1 || channels == 2) && frames > 0 &&
                int64(frames) * channels <= 8000000 && rate >= 8000 &&
                rate <= 384000,
            "Unsupported processing bounds");
    require(component->getBusCount(kAudio, kInput) >= 1 &&
                component->getBusCount(kAudio, kOutput) >= 1,
            "Effect must have main input and output");
    auto arrangement = channels == 1 ? SpeakerArr::kMono : SpeakerArr::kStereo;
    require(processor->setBusArrangements(&arrangement, 1, &arrangement, 1) ==
                kResultTrue,
            "Plugin rejected mono/stereo main-only layout");
    for (auto dir : {kInput, kOutput}) {
      BusInfo main{};
      require(component->getBusInfo(kAudio, dir, 0, main) == kResultOk &&
                  main.channelCount == channels,
              "Main bus layout mismatch");
      for (int32 i = 0; i < component->getBusCount(kAudio, dir); ++i)
        require(component->activateBus(kAudio, dir, i, i == 0) == kResultOk,
                "Bus activation failed");
      for (int32 i = 0; i < component->getBusCount(kEvent, dir); ++i)
        component->activateBus(kEvent, dir, i, false);
    }
    loadstate(component, ctrl, req.value("state", json(nullptr)));
    if (ctrl) {
      ResizableMemoryIBStream sync;
      require(component->getState(&sync) == kResultOk,
              "State sync unavailable");
      sync.rewind();
      ctrl->setComponentState(&sync);
    }
    constexpr int block = 512;
    ProcessSetup setup{kOffline, kSample32, block, rate};
    require(processor->setupProcessing(setup) == kResultOk,
            "Offline setup failed");
    ParameterChanges changes;
    std::vector<ParamID> ids;
    if (ctrl)
      for (int32 i = 0; i < ctrl->getParameterCount(); ++i) {
        ParameterInfo p{};
        ctrl->getParameterInfo(i, p);
        ids.push_back(p.id);
      }
    auto values = req.value("parameters", json::object());
    for (auto it = values.begin(); it != values.end(); ++it) {
      ParamID id = static_cast<ParamID>(std::stoul(it.key()));
      double value = it.value();
      require(ctrl && std::find(ids.begin(), ids.end(), id) != ids.end() &&
                  std::isfinite(value) && value >= 0 && value <= 1,
              "Unknown/invalid normalized parameter");
      require(ctrl->setParamNormalized(id, value) == kResultOk,
              "Parameter rejected");
      int32 index = 0;
      auto q = changes.addParameterData(id, index);
      q->addPoint(0, value, index);
    }
    auto latency = processor->getLatencySamples();
    auto tail = processor->getTailSamples();
    require(latency <= static_cast<uint32>(rate * 10),
            "Latency exceeds 10 seconds");
    int tailFrames = 0;
    if (req.contains("tail_seconds"))
      tailFrames =
          static_cast<int>(req.at("tail_seconds").get<double>() * rate);
    else {
      require(tail != kInfiniteTail && tail <= rate * 10,
              "Specify an explicit <=10s tail for infinite/long-tail effects");
      tailFrames = tail;
    }
    require(tailFrames >= 0 && tailFrames <= rate * 10,
            "Tail exceeds 10 seconds");
    int total = frames + latency + tailFrames;
    require(int64(total) * channels <= 8000000,
            "Output exceeds 8M-sample budget");
    std::vector<float> input(static_cast<size_t>(frames) * channels),
        out(static_cast<size_t>(total) * channels);
    std::ifstream in(
        std::filesystem::u8path(req.at("input").get<std::string>()),
        std::ios::binary);
    in.read(reinterpret_cast<char *>(input.data()),
            input.size() * sizeof(float));
    require(in.gcount() == static_cast<std::streamsize>(input.size() * 4),
            "Input length mismatch");
    HostProcessData data;
    require(data.prepare(*component, block, kSample32), "Buffer setup failed");
    data.processMode = kOffline;
    data.inputParameterChanges = &changes;
    ParameterChanges outgoing;
    data.outputParameterChanges = &outgoing;
    ProcessContext pc{};
    pc.sampleRate = rate;
    pc.state = ProcessContext::kPlaying;
    data.processContext = &pc;
    require(component->setActive(true) == kResultOk, "Activation failed");
    auto started = processor->setProcessing(true);
    require(started == kResultOk || started == kNotImplemented,
            "Processing start failed");
    for (int pos = 0; pos < total; pos += block) {
      int count = std::min(block, total - pos);
      data.numSamples = count;
      pc.projectTimeSamples = pos;
      for (int b = 0; b < data.numInputs; ++b) {
        data.inputs[b].silenceFlags = 0;
        for (int ch = 0; ch < data.inputs[b].numChannels; ++ch)
          for (int i = 0; i < count; ++i)
            data.inputs[b].channelBuffers32[ch][i] =
                (b == 0 && pos + i < frames) ? input[(pos + i) * channels + ch]
                                             : 0;
      }
      for (int b = 0; b < data.numOutputs; ++b) {
        data.outputs[b].silenceFlags = 0;
        for (int ch = 0; ch < data.outputs[b].numChannels; ++ch)
          std::fill_n(data.outputs[b].channelBuffers32[ch], count, 0.f);
      }
      require(processor->process(data) == kResultOk,
              "Plugin processing failed");
      require(processor->getLatencySamples() == latency,
              "Dynamic latency change unsupported; render aborted");
      for (int i = 0; i < count; ++i)
        for (int ch = 0; ch < channels; ++ch) {
          float v = data.outputs[0].channelBuffers32[ch][i];
          require(std::isfinite(v), "Nonfinite plugin output");
          out[(pos + i) * channels + ch] = v;
        }
      if (ctrl)
        for (int i = 0; i < outgoing.getParameterCount(); ++i) {
          auto q = outgoing.getParameterData(i);
          int32 offset;
          double v;
          if (q && q->getPointCount() > 0 &&
              q->getPoint(q->getPointCount() - 1, offset, v) == kResultOk)
            ctrl->setParamNormalized(q->getParameterId(), v);
        }
      changes.clearQueue();
      outgoing.clearQueue();
    }
    processor->setProcessing(false);
    component->setActive(false);
    std::ofstream file(
        std::filesystem::u8path(req.at("output").get<std::string>()),
        std::ios::binary);
    file.write(reinterpret_cast<char *>(out.data() + latency * channels),
               (frames + tailFrames) * channels * 4);
    require(bool(file), "Output write failed");
    json result = inspect(*provider);
    result["state"] = state(component);
    result["class_id"] = id;
    tail = processor->getTailSamples();
    result["frames"] = frames + tailFrames;
    result["latency_samples"] = latency;
    result["reported_tail_samples"] = tail;
    result["rendered_tail_samples"] = tailFrames;
    result["tail_truncated"] =
        tail == kInfiniteTail || tail > static_cast<uint32>(tailFrames);
    result["latency_compensated"] = true;
    return result;
  }
  if (req.value("mode", std::string("scan")) == "scan")
    return {{"classes", classes}, {"protocol", 1}};
  throw std::runtime_error("Selected VST3 effect class not found");
}
static int entry(int argc, char **argv) {
#ifdef _WIN32
  CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
#endif
  if (argc != 3)
    return 2;
  try {
    std::ifstream f(std::filesystem::u8path(argv[1]));
    json req;
    f >> req;
    auto result = run(req);
    std::ofstream(std::filesystem::u8path(argv[2]))
        << json({{"result", result}}).dump();
    return 0;
  } catch (const std::exception &e) {
    std::ofstream(std::filesystem::u8path(argv[2]))
        << json({{"error", e.what()}}).dump();
    return 1;
  }
}

#ifdef _WIN32
int wmain(int argc, wchar_t **argv) {
  std::vector<std::string> utf8;
  std::vector<char *> raw;
  for (int i = 0; i < argc; ++i)
    utf8.push_back(std::filesystem::path(argv[i]).u8string());
  for (auto &value : utf8)
    raw.push_back(value.data());
  return entry(argc, raw.data());
}
#else
int main(int argc, char **argv) { return entry(argc, argv); }
#endif
