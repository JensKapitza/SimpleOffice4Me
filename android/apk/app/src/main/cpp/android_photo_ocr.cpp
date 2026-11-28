#include <jni.h>
#include <tesseract/capi.h>

#include <chrono>
#include <cstdint>
#include <cstring>
#include <memory>
#include <vector>

namespace {
constexpr jint kMaxEdge = 2800;
constexpr jint kMaxWaitMs = 60000;
constexpr size_t kMaxTextBytes = 400000;

jstring decodeUtf8(JNIEnv* env, const char* text) {
    const size_t size = strnlen(text, kMaxTextBytes + 1);
    if (size > kMaxTextBytes) {
        return nullptr;
    }
    // JNI NewStringUTF expects modified UTF-8, not the standard UTF-8 OCR emits.
    jbyteArray bytes = env->NewByteArray(static_cast<jsize>(size));
    if (bytes == nullptr) {
        return nullptr;
    }
    if (size > 0) {
        env->SetByteArrayRegion(bytes, 0, static_cast<jsize>(size),
                                reinterpret_cast<const jbyte*>(text));
        if (env->ExceptionCheck()) {
            return nullptr;
        }
    }
    jclass stringClass = env->FindClass("java/lang/String");
    if (stringClass == nullptr) {
        return nullptr;
    }
    jmethodID init = env->GetMethodID(stringClass, "<init>", "([BLjava/lang/String;)V");
    if (init == nullptr) {
        return nullptr;
    }
    jstring charset = env->NewStringUTF("UTF-8");
    return charset == nullptr ? nullptr :
        static_cast<jstring>(env->NewObject(stringClass, init, bytes, charset));
}
}  // namespace

extern "C" JNIEXPORT jstring JNICALL
Java_de_simpleoffice4me_android_AndroidPhotoOcr_recognizeNative(
        JNIEnv* env, jclass, jstring tessdataPath, jbyteArray raster,
        jint width, jint height, jint timeoutMillis) {
    if (tessdataPath == nullptr || raster == nullptr ||
        width < 1 || width > kMaxEdge || height < 1 || height > kMaxEdge ||
        timeoutMillis < 1 || timeoutMillis > kMaxWaitMs ||
        env->GetArrayLength(raster) != width * height) {
        return nullptr;
    }

    try {
        // This path is generated from the APK's own private, verified model
        // directory; the raster contains bytes only, never encoded files.
        const char* pathChars = env->GetStringUTFChars(tessdataPath, nullptr);
        if (pathChars == nullptr) {
            return nullptr;
        }
        const auto release = [env, tessdataPath](const char* value) {
            env->ReleaseStringUTFChars(tessdataPath, value);
        };
        std::unique_ptr<const char, decltype(release)> path(pathChars, release);
        std::vector<uint8_t> pixels(static_cast<size_t>(width) * height);
        env->GetByteArrayRegion(raster, 0, width * height,
                                reinterpret_cast<jbyte*>(pixels.data()));
        if (env->ExceptionCheck()) {
            return nullptr;
        }

        const std::unique_ptr<TessBaseAPI, decltype(&TessBaseAPIDelete)> engine(
            TessBaseAPICreate(), TessBaseAPIDelete);
        if (!engine || TessBaseAPIInit2(engine.get(), path.get(), "deu+eng", tesseract::OEM_LSTM_ONLY) != 0) {
            return nullptr;
        }
        TessBaseAPISetVariable(engine.get(), "debug_file", "/dev/null");
        TessBaseAPISetPageSegMode(engine.get(), tesseract::PSM_AUTO);
        TessBaseAPISetImage(engine.get(), pixels.data(), width, height, 1, width);
        const std::unique_ptr<ETEXT_DESC, decltype(&TessMonitorDelete)> timer(
            TessMonitorCreate(), TessMonitorDelete);
        if (!timer) {
            return nullptr;
        }
        TessMonitorSetDeadlineMSecs(timer.get(), timeoutMillis);
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeoutMillis);
        if (TessBaseAPIRecognize(engine.get(), timer.get()) != 0 ||
            std::chrono::steady_clock::now() >= deadline) {
            return nullptr;
        }
        const std::unique_ptr<char[], decltype(&TessDeleteText)> text(
            TessBaseAPIGetUTF8Text(engine.get()), TessDeleteText);
        return text ? decodeUtf8(env, text.get()) : nullptr;
    } catch (...) {
        // Never cross JNI with a C++ exception or log source text/paths.
        return nullptr;
    }
}
