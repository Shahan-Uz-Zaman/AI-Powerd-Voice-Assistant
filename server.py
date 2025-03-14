import socket
import wave
import webrtcvad
import collections
import time
import speech_recognition as sr
import google.generativeai as genai
from gtts import gTTS
import os
import re
import threading

from pydub import AudioSegment
from pydub.playback import play

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1
FRAME_DURATION = 30  # ms
VAD_AGGRESSIVENESS = 3
UDP_IP = '0.0.0.0'
UDP_PORT = 5000
SILENCE_HOLD_DURATION = 1.0

vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)

genai.configure(api_key="AIzaSyARW65-WNqAB8fkjvjOy8IpX0jXTt1o35I")

is_processing = threading.Lock()


def send_udp_command(command):
    UDP_IP = "192.168.81.7"
    UDP_PORT = 12345
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(command.encode(), (UDP_IP, UDP_PORT))
    sock.close()


def is_speech(frame):
    return vad.is_speech(frame, SAMPLE_RATE)


def save_audio(audio_data, filename='question.wav'):
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_data)
        print(f"[INFO] Saved audio to {filename}")
    return filename


def transcribe_audio(filename):
    recognizer = sr.Recognizer()
    with sr.AudioFile(filename) as source:
        audio = recognizer.record(source)
    try:
        text = recognizer.recognize_google(audio)
        print(f"[TRANSCRIPTION] {text}")
        return text
    except sr.UnknownValueError:
        print("[ERROR] Could not understand audio")
    except sr.RequestError as e:
        print(f"[ERROR] Could not request results: {e}")
    return None


def speak_text_in_chunks(text, lang='en-in', speed=1.4):
    sentences = re.split(r'(?<=[.!?]) +', text)

    for sentence in sentences:
        if not sentence.strip():
            continue

        print(f"[SPEAKING] {sentence}")

        tts = gTTS(text=sentence, lang=lang)
        tts.save("temp.mp3")

        duration = estimate_tts_duration(sentence, speed)

        send_udp_command(f"processing {duration}")

        os.system(f"mpv --af=rubberband=pitch-scale=1.0 --no-video --speed={speed} temp.mp3")


def clean_response(text):
    cleaned_text = re.sub(r'[*]', '', text)
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    return cleaned_text


def get_gemini_response(user_input):
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
        You are Nalpo, a professional assistant. Respond in Hindi if I speak in Hindi; otherwise, respond in English.
        Here's what I said: "{user_input}"
        """
        response = model.generate_content(prompt, generation_config={"max_output_tokens": 150})
        return response.candidates[0].content.parts[0].text
    except Exception as e:
        print(f"[ERROR] Gemini API request failed: {e}")
        return "I am sorry, I couldn't process that."


def estimate_tts_duration(text, speed=1.4):
    words = len(text.split())
    words_per_second = (150 * speed) / 60
    duration = words / words_per_second
    return int(duration * 1000)


def check_and_send_movement(transcription):
    directions = ["left", "right", "up", "down"]
    words = transcription.lower().split()

    for i in range(len(words) - 1):
        if (words[i] == "mov" or words[i] == "move") and words[i + 1] in directions:
            direction = words[i + 1]
            command = f"mov {direction}"
            print(f"Detected movement command: {command}")
            send_udp_command(command)
            return True
    return False


def process_audio_stream():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    print(f"[INFO] Listening on {UDP_IP}:{UDP_PORT}...")

    audio_buffer = b""
    is_recording = False
    frame_size = int(SAMPLE_RATE * FRAME_DURATION / 1000) * SAMPLE_WIDTH
    ring_buffer = collections.deque(maxlen=10)
    last_voiced_time = None
    speech_buffer = []

    while True:
        data, addr = sock.recvfrom(1024)
        audio_buffer += data

        while len(audio_buffer) >= frame_size:
            frame = audio_buffer[:frame_size]
            audio_buffer = audio_buffer[frame_size:]

            voiced = is_speech(frame)
            ring_buffer.append(voiced)

            if voiced:
                last_voiced_time = time.time()

            if not is_recording and any(ring_buffer):
                send_udp_command("voicedetected 2000")
                print("[INFO] Voice detected. Starting recording...")
                is_recording = True
                speech_buffer = [frame]

            elif is_recording:
                speech_buffer.append(frame)

                if last_voiced_time and time.time() - last_voiced_time > SILENCE_HOLD_DURATION:
                    print("[INFO] Silence detected. Stopping recording...")
                    is_recording = False
                    audio_data = b"".join(speech_buffer)
                    filename = save_audio(audio_data)
                    speech_buffer.clear()

                    transcription = transcribe_audio(filename)
                    if transcription:
                        if check_and_send_movement(transcription):
                            print("Movement command detected. Skipping Gemini processing.")
                        else:
                            transcription += ", Explain in 1-3 sentences."

                            with is_processing:
                                response_text = get_gemini_response(transcription)

                                print(f"[GEMINI RESPONSE] {response_text}")

                                speak_text_in_chunks(response_text, speed=1.4)


if __name__ == '__main__':
    process_audio_stream()
