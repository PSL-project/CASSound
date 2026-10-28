// Test-only effect: gain, fixed delay and decaying tail. Crash/hang modes
// exercise process isolation.
#include "pluginterfaces/base/ibstream.h"
#include "pluginterfaces/vst/ivstparameterchanges.h"
#include "public.sdk/source/main/pluginfactory.h"
#include "public.sdk/source/vst/vstsinglecomponenteffect.h"
#include <array>
#include <chrono>
#include <cstdlib>
#include <thread>
using namespace Steinberg;
using namespace Steinberg::Vst;
class TestFX : public SingleComponentEffect {
  double gain = .5;
  int mode = 0;
  int pos = 0;
  std::array<std::array<float, 7>, 2> delay{};
  std::array<float, 2> tail{};

public:
  static FUnknown *create(void *) {
    return static_cast<IAudioProcessor *>(new TestFX);
  }
  tresult PLUGIN_API initialize(FUnknown *host) override {
    auto r = SingleComponentEffect::initialize(host);
    if (r != kResultOk)
      return r;
    addAudioInput(STR16("Input"), SpeakerArr::kStereo);
    addAudioOutput(STR16("Output"), SpeakerArr::kStereo);
    parameters.addParameter(STR16("Gain"), nullptr, 0, .5,
                            ParameterInfo::kCanAutomate, 1);
    parameters.addParameter(STR16("Test mode"), nullptr, 2, 0,
                            ParameterInfo::kCanAutomate, 2);
    return kResultOk;
  }
  tresult PLUGIN_API setProcessing(TBool) override { return kResultOk; }
  tresult PLUGIN_API setComponentState(IBStream *s) override {
    return setState(s);
  }
  uint32 PLUGIN_API getLatencySamples() override { return 7; }
  uint32 PLUGIN_API getTailSamples() override { return 64; }
  tresult PLUGIN_API setActive(TBool active) override {
    pos = 0;
    delay = {};
    tail = {};
    return SingleComponentEffect::setActive(active);
  }
  tresult PLUGIN_API getState(IBStream *s) override {
    s->write(&gain, sizeof(gain), nullptr);
    return kResultOk;
  }
  tresult PLUGIN_API setState(IBStream *s) override {
    int32 count;
    s->read(&gain, sizeof(gain), &count);
    setParamNormalized(1, gain);
    return count == sizeof(gain) ? kResultOk : kResultFalse;
  }
  tresult PLUGIN_API process(ProcessData &d) override {
    if (d.inputParameterChanges)
      for (int i = 0; i < d.inputParameterChanges->getParameterCount(); ++i) {
        auto q = d.inputParameterChanges->getParameterData(i);
        int32 offset;
        double v;
        q->getPoint(q->getPointCount() - 1, offset, v);
        if (q->getParameterId() == 1)
          gain = v;
        if (q->getParameterId() == 2)
          mode = int(v * 2 + .5);
      }
    if (mode == 1)
      std::abort();
    if (mode == 2)
      std::this_thread::sleep_for(std::chrono::seconds(60));
    if (!d.numInputs || !d.numOutputs)
      return kResultOk;
    for (int i = 0; i < d.numSamples; ++i) {
      for (int ch = 0; ch < d.outputs[0].numChannels; ++ch) {
        auto v = d.inputs[0].channelBuffers32[ch][i];
        float delayed = delay[ch][pos];
        delay[ch][pos] = v * gain;
        tail[ch] = .5f * tail[ch] + delayed;
        d.outputs[0].channelBuffers32[ch][i] = tail[ch];
      }
      pos = (pos + 1) % 7;
    }
    return kResultOk;
  }
};
BEGIN_FACTORY_DEF("CASSound", "https://example.invalid",
                  "build@cassound.invalid")
DEF_CLASS2(INLINE_UID(0x43415353, 0x4F554E44, 0x54535446, 0x58000001),
           PClassInfo::kManyInstances, kVstAudioEffectClass, "CASSound Test FX",
           0, "Fx", "1.0.0", kVstVersionString, TestFX::create)
END_FACTORY
