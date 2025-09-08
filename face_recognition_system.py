#!/usr/bin/env python3
"""
Face Recognition Embedded System for Attendance
- Scans every 10 minutes
- Requires 3+ detections to mark a student present
- Integrates with the main attendance database
"""

import cv2
import face_recognition
import numpy as np
import time
import requests
import json
import logging
import os
from datetime import datetime, timedelta
import threading
from collections import defaultdict, deque
import pickle
import sqlite3
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class FaceRecognitionAttendanceSystem:
    def __init__(self, config_file='face_config.json'):
        """Initialize the face recognition attendance system"""
        self.load_config(config_file)
        
        # System settings
        self.SCAN_INTERVAL = 10 * 60  # 10 minutes in seconds
        self.REQUIRED_DETECTIONS = 3  # Minimum detections to mark present
        self.CONFIDENCE_THRESHOLD = 0.6  # Face recognition confidence threshold
        self.DETECTION_WINDOW = 30 * 60  # 30 minutes window for collecting detections
        
        # Data structures
        self.known_face_encodings = []
        self.known_face_names = []
        self.known_face_ids = []
        self.student_detections = defaultdict(lambda: deque(maxlen=10))
        self.confirmed_attendance = set()
        
        # System state
        self.running = False
        self.camera = None
        self.current_session = None
        
        # Initialize components
        self.init_database()
        self.load_known_faces()
        
        logger.info("Face Recognition Attendance System initialized")

    def load_config(self, config_file):
        """Load configuration from JSON file"""
        default_config = {
            "api_base_url": "https://gameocoder-backend.onrender.com",
            "camera_index": 0,
            "faces_directory": "known_faces",
            "local_db_path": "face_attendance.db",
            "api_timeout": 30,
            "max_face_distance": 0.6,
            "frame_skip": 30  # Process every 30th frame for performance
        }
        
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    loaded_config = json.load(f)
                default_config.update(loaded_config)
        except Exception as e:
            logger.warning(f"Could not load config file: {e}. Using defaults.")
        
        self.config = default_config
        self.API_BASE_URL = self.config['api_base_url']

    def init_database(self):
        """Initialize local SQLite database for offline storage"""
        try:
            self.local_db_path = self.config['local_db_path']
            with sqlite3.connect(self.local_db_path) as conn:
                cursor = conn.cursor()
                
                # Create tables
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS face_detections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        student_id TEXT NOT NULL,
                        student_name TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        timestamp DATETIME NOT NULL,
                        session_id TEXT,
                        synced BOOLEAN DEFAULT FALSE
                    )
                ''')
                
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS attendance_confirmations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        student_id TEXT NOT NULL,
                        student_name TEXT NOT NULL,
                        detection_count INTEGER NOT NULL,
                        first_detection DATETIME NOT NULL,
                        confirmed_at DATETIME NOT NULL,
                        session_id TEXT,
                        synced BOOLEAN DEFAULT FALSE
                    )
                ''')
                
                conn.commit()
                logger.info("Local database initialized successfully")
                
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")

    def load_known_faces(self):
        """Load known faces from directory or database"""
        faces_dir = Path(self.config['faces_directory'])
        
        if not faces_dir.exists():
            logger.warning(f"Faces directory {faces_dir} does not exist. Creating it...")
            faces_dir.mkdir(parents=True, exist_ok=True)
        
        # Try to load from pickle file first (faster)
        pickle_file = faces_dir / 'face_encodings.pkl'
        if pickle_file.exists():
            try:
                with open(pickle_file, 'rb') as f:
                    data = pickle.load(f)
                    self.known_face_encodings = data['encodings']
                    self.known_face_names = data['names']
                    self.known_face_ids = data['ids']
                logger.info(f"Loaded {len(self.known_face_encodings)} known faces from pickle file")
                return
            except Exception as e:
                logger.warning(f"Failed to load pickle file: {e}")
        
        # Load from individual image files
        self.load_faces_from_images(faces_dir)
        
        # If no local faces, try to download from API
        if len(self.known_face_encodings) == 0:
            self.download_student_faces()

    def load_faces_from_images(self, faces_dir):
        """Load face encodings from image files"""
        supported_formats = {'.jpg', '.jpeg', '.png', '.bmp'}
        
        for image_file in faces_dir.iterdir():
            if image_file.suffix.lower() in supported_formats:
                try:
                    # Extract student info from filename (e.g., "2500032073_Nitin_Singh.jpg")
                    name_parts = image_file.stem.split('_')
                    if len(name_parts) >= 2:
                        student_id = name_parts[0]
                        student_name = ' '.join(name_parts[1:]).replace('_', ' ')
                    else:
                        student_id = image_file.stem
                        student_name = image_file.stem
                    
                    # Load and encode face
                    image = face_recognition.load_image_file(str(image_file))
                    encodings = face_recognition.face_encodings(image)
                    
                    if encodings:
                        self.known_face_encodings.append(encodings[0])
                        self.known_face_names.append(student_name)
                        self.known_face_ids.append(student_id)
                        logger.info(f"Loaded face for {student_name} ({student_id})")
                    else:
                        logger.warning(f"No face found in {image_file}")
                        
                except Exception as e:
                    logger.error(f"Failed to load face from {image_file}: {e}")
        
        # Save to pickle file for faster loading next time
        if self.known_face_encodings:
            self.save_face_encodings_pickle(faces_dir)

    def save_face_encodings_pickle(self, faces_dir):
        """Save face encodings to pickle file"""
        try:
            pickle_file = faces_dir / 'face_encodings.pkl'
            data = {
                'encodings': self.known_face_encodings,
                'names': self.known_face_names,
                'ids': self.known_face_ids
            }
            with open(pickle_file, 'wb') as f:
                pickle.dump(data, f)
            logger.info(f"Saved {len(self.known_face_encodings)} face encodings to pickle file")
        except Exception as e:
            logger.error(f"Failed to save face encodings: {e}")

    def download_student_faces(self):
        """Download student face images from the API"""
        try:
            response = requests.get(f"{self.API_BASE_URL}/students/faces", timeout=self.config['api_timeout'])
            if response.status_code == 200:
                students_data = response.json()
                faces_dir = Path(self.config['faces_directory'])
                
                for student in students_data.get('students', []):
                    if 'face_image_url' in student:
                        self.download_student_face(student, faces_dir)
                        
            else:
                logger.warning(f"Failed to fetch student faces from API: {response.status_code}")
                
        except requests.RequestException as e:
            logger.warning(f"Could not connect to API for student faces: {e}")
        except Exception as e:
            logger.error(f"Error downloading student faces: {e}")

    def download_student_face(self, student, faces_dir):
        """Download individual student face image"""
        try:
            image_url = student['face_image_url']
            student_id = student.get('id_number', 'unknown')
            student_name = student.get('name', 'unknown').replace(' ', '_')
            
            response = requests.get(image_url, timeout=10)
            if response.status_code == 200:
                image_file = faces_dir / f"{student_id}_{student_name}.jpg"
                with open(image_file, 'wb') as f:
                    f.write(response.content)
                
                # Process the downloaded image
                image = face_recognition.load_image_file(str(image_file))
                encodings = face_recognition.face_encodings(image)
                
                if encodings:
                    self.known_face_encodings.append(encodings[0])
                    self.known_face_names.append(student['name'])
                    self.known_face_ids.append(student_id)
                    logger.info(f"Downloaded and processed face for {student['name']}")
                
        except Exception as e:
            logger.error(f"Failed to download face for {student.get('name', 'unknown')}: {e}")

    def start_session(self, session_info):
        """Start a face recognition session"""
        self.current_session = {
            'session_id': session_info.get('session_id', f"session_{int(time.time())}"),
            'schedule_id': session_info.get('schedule_id'),
            'start_time': datetime.now(),
            'class_info': session_info.get('class_info', {})
        }
        
        # Clear previous session data
        self.student_detections.clear()
        self.confirmed_attendance.clear()
        
        logger.info(f"Started face recognition session: {self.current_session['session_id']}")

    def initialize_camera(self):
        """Initialize camera for face recognition"""
        try:
            self.camera = cv2.VideoCapture(self.config['camera_index'])
            
            if not self.camera.isOpened():
                logger.error("Failed to open camera")
                return False
            
            # Set camera properties for better performance
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.camera.set(cv2.CAP_PROP_FPS, 30)
            
            logger.info("Camera initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize camera: {e}")
            return False

    def start_continuous_recognition(self):
        """Start continuous face recognition with 10-minute intervals"""
        if not self.initialize_camera():
            return False
        
        self.running = True
        
        # Start recognition thread
        recognition_thread = threading.Thread(target=self._recognition_loop, daemon=True)
        recognition_thread.start()
        
        # Start periodic processing thread
        processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
        processing_thread.start()
        
        logger.info("Started continuous face recognition system")
        return True

    def _recognition_loop(self):
        """Main recognition loop - processes frames continuously"""
        frame_count = 0
        
        while self.running:
            try:
                ret, frame = self.camera.read()
                if not ret:
                    logger.warning("Failed to read frame from camera")
                    time.sleep(1)
                    continue
                
                frame_count += 1
                
                # Skip frames for performance (process every Nth frame)
                if frame_count % self.config['frame_skip'] != 0:
                    continue
                
                # Process frame for face recognition
                self.process_frame(frame)
                
                # Small delay to prevent excessive CPU usage
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Error in recognition loop: {e}")
                time.sleep(1)

    def _processing_loop(self):
        """Periodic processing loop - runs every 10 minutes"""
        while self.running:
            try:
                # Wait for the scan interval
                time.sleep(self.SCAN_INTERVAL)
                
                # Process accumulated detections
                self.process_attendance_confirmations()
                
                # Sync with remote database
                self.sync_with_remote_database()
                
                logger.info(f"Completed 10-minute processing cycle")
                
            except Exception as e:
                logger.error(f"Error in processing loop: {e}")

    def process_frame(self, frame):
        """Process a single frame for face detection and recognition"""
        try:
            # Resize frame for faster processing
            small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
            rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
            
            # Find faces in the frame
            face_locations = face_recognition.face_locations(rgb_small_frame)
            face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
            
            current_time = datetime.now()
            
            # Process each detected face
            for face_encoding in face_encodings:
                # Compare with known faces
                matches = face_recognition.compare_faces(
                    self.known_face_encodings, 
                    face_encoding, 
                    tolerance=self.config['max_face_distance']
                )
                
                # Calculate face distances
                face_distances = face_recognition.face_distance(
                    self.known_face_encodings, 
                    face_encoding
                )
                
                # Find best match
                if len(face_distances) > 0:
                    best_match_index = np.argmin(face_distances)
                    
                    if matches[best_match_index] and face_distances[best_match_index] < self.config['max_face_distance']:
                        student_id = self.known_face_ids[best_match_index]
                        student_name = self.known_face_names[best_match_index]
                        confidence = 1 - face_distances[best_match_index]
                        
                        # Record detection
                        self.record_face_detection(student_id, student_name, confidence, current_time)
                        
        except Exception as e:
            logger.error(f"Error processing frame: {e}")

    def record_face_detection(self, student_id, student_name, confidence, timestamp):
        """Record a face detection"""
        try:
            # Add to in-memory tracking
            detection_data = {
                'timestamp': timestamp,
                'confidence': confidence,
                'student_name': student_name
            }
            
            self.student_detections[student_id].append(detection_data)
            
            # Store in local database
            with sqlite3.connect(self.local_db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO face_detections 
                    (student_id, student_name, confidence, timestamp, session_id)
                    VALUES (?, ?, ?, ?, ?)
                ''', (student_id, student_name, confidence, timestamp, 
                      self.current_session['session_id'] if self.current_session else None))
                conn.commit()
            
            logger.debug(f"Recorded detection for {student_name} ({student_id}) - confidence: {confidence:.2f}")
            
        except Exception as e:
            logger.error(f"Failed to record detection: {e}")

    def process_attendance_confirmations(self):
        """Process detections and confirm attendance for students with 3+ detections"""
        current_time = datetime.now()
        window_start = current_time - timedelta(seconds=self.DETECTION_WINDOW)
        
        newly_confirmed = []
        
        for student_id, detections in self.student_detections.items():
            # Filter detections within the time window
            recent_detections = [d for d in detections if d['timestamp'] >= window_start]
            
            # Check if student meets confirmation criteria
            if (len(recent_detections) >= self.REQUIRED_DETECTIONS and 
                student_id not in self.confirmed_attendance):
                
                # Confirm attendance
                self.confirmed_attendance.add(student_id)
                
                # Calculate average confidence
                avg_confidence = sum(d['confidence'] for d in recent_detections) / len(recent_detections)
                first_detection = min(d['timestamp'] for d in recent_detections)
                student_name = recent_detections[0]['student_name']
                
                # Store confirmation in database
                self.store_attendance_confirmation(
                    student_id, student_name, len(recent_detections),
                    first_detection, current_time, avg_confidence
                )
                
                newly_confirmed.append({
                    'student_id': student_id,
                    'student_name': student_name,
                    'detection_count': len(recent_detections),
                    'avg_confidence': avg_confidence
                })
                
                logger.info(f"ATTENDANCE CONFIRMED: {student_name} ({student_id}) - "
                           f"{len(recent_detections)} detections, avg confidence: {avg_confidence:.2f}")
        
        # Send newly confirmed attendance to API
        if newly_confirmed:
            self.send_attendance_to_api(newly_confirmed)

    def store_attendance_confirmation(self, student_id, student_name, detection_count,
                                    first_detection, confirmed_at, avg_confidence):
        """Store attendance confirmation in local database"""
        try:
            with sqlite3.connect(self.local_db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO attendance_confirmations 
                    (student_id, student_name, detection_count, first_detection, 
                     confirmed_at, session_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (student_id, student_name, detection_count, first_detection,
                      confirmed_at, self.current_session['session_id'] if self.current_session else None))
                conn.commit()
                
        except Exception as e:
            logger.error(f"Failed to store attendance confirmation: {e}")

    def send_attendance_to_api(self, confirmed_students):
        """Send confirmed attendance to the remote API"""
        try:
            if not self.current_session:
                logger.warning("No active session - cannot send attendance to API")
                return
            
            attendance_data = []
            for student in confirmed_students:
                attendance_data.append({
                    'person_id': student['student_id'],  # Using student_id as person_id
                    'student_name': student['student_name'],
                    'method': 'face_recognition',
                    'confidence_score': student['avg_confidence'],
                    'detection_count': student['detection_count'],
                    'notes': f"Face recognition: {student['detection_count']} detections over 10+ minute period"
                })
            
            payload = {
                'schedule_id': self.current_session.get('schedule_id'),
                'session_id': self.current_session['session_id'],
                'attendance_data': attendance_data,
                'method': 'face_recognition_embedded'
            }
            
            # Send to face recognition specific endpoint
            response = requests.post(
                f"{self.API_BASE_URL}/face/confirm-attendance",
                json=payload,
                timeout=self.config['api_timeout']
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    logger.info(f"Successfully sent attendance for {len(confirmed_students)} students to API")
                    self.mark_as_synced(confirmed_students)
                else:
                    logger.warning(f"API returned error: {result.get('message', 'Unknown error')}")
            else:
                logger.warning(f"Failed to send attendance to API: HTTP {response.status_code}")
                
        except requests.RequestException as e:
            logger.warning(f"Could not connect to API: {e}")
        except Exception as e:
            logger.error(f"Error sending attendance to API: {e}")

    def mark_as_synced(self, confirmed_students):
        """Mark attendance confirmations as synced with remote database"""
        try:
            with sqlite3.connect(self.local_db_path) as conn:
                cursor = conn.cursor()
                for student in confirmed_students:
                    cursor.execute('''
                        UPDATE attendance_confirmations 
                        SET synced = TRUE 
                        WHERE student_id = ? AND session_id = ? AND synced = FALSE
                    ''', (student['student_id'], self.current_session['session_id']))
                conn.commit()
                
        except Exception as e:
            logger.error(f"Failed to mark records as synced: {e}")

    def sync_with_remote_database(self):
        """Sync any unsynced attendance confirmations with remote database"""
        try:
            with sqlite3.connect(self.local_db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT student_id, student_name, detection_count, confirmed_at
                    FROM attendance_confirmations 
                    WHERE synced = FALSE
                ''')
                
                unsynced_records = cursor.fetchall()
                
                if unsynced_records:
                    logger.info(f"Found {len(unsynced_records)} unsynced records to sync")
                    
                    # Convert to format expected by API
                    confirmed_students = []
                    for record in unsynced_records:
                        confirmed_students.append({
                            'student_id': record[0],
                            'student_name': record[1],
                            'detection_count': record[2],
                            'avg_confidence': 0.85  # Default confidence for syncing
                        })
                    
                    # Try to send to API
                    self.send_attendance_to_api(confirmed_students)
                    
        except Exception as e:
            logger.error(f"Error during sync: {e}")

    def stop_recognition(self):
        """Stop the face recognition system"""
        self.running = False
        
        if self.camera:
            self.camera.release()
            
        logger.info("Face recognition system stopped")

    def get_session_stats(self):
        """Get current session statistics"""
        if not self.current_session:
            return None
        
        stats = {
            'session_id': self.current_session['session_id'],
            'start_time': self.current_session['start_time'],
            'total_detections': sum(len(detections) for detections in self.student_detections.values()),
            'unique_students_detected': len(self.student_detections),
            'confirmed_attendance': len(self.confirmed_attendance),
            'students_detected': []
        }
        
        # Add details for each detected student
        for student_id, detections in self.student_detections.items():
            student_info = {
                'student_id': student_id,
                'detection_count': len(detections),
                'confirmed': student_id in self.confirmed_attendance,
                'avg_confidence': sum(d['confidence'] for d in detections) / len(detections) if detections else 0
            }
            if detections:
                student_info['student_name'] = detections[0]['student_name']
                student_info['first_detection'] = min(d['timestamp'] for d in detections)
                student_info['last_detection'] = max(d['timestamp'] for d in detections)
            
            stats['students_detected'].append(student_info)
        
        return stats

def main():
    """Main function to run the face recognition system"""
    # Create configuration file if it doesn't exist
    config_file = 'face_config.json'
    if not os.path.exists(config_file):
        default_config = {
            "api_base_url": "https://gameocoder-backend.onrender.com",
            "camera_index": 0,
            "faces_directory": "known_faces",
            "local_db_path": "face_attendance.db",
            "api_timeout": 30,
            "max_face_distance": 0.6,
            "frame_skip": 30
        }
        with open(config_file, 'w') as f:
            json.dump(default_config, f, indent=4)
        print(f"Created default configuration file: {config_file}")
    
    # Initialize system
    face_system = FaceRecognitionAttendanceSystem(config_file)
    
    # Start session (in production, this would be triggered by API or schedule)
    session_info = {
        'session_id': f"face_session_{int(time.time())}",
        'schedule_id': 1,  # This would come from the API
        'class_info': {
            'subject': 'Computer Science',
            'section': 'S33'
        }
    }
    
    face_system.start_session(session_info)
    
    # Start recognition
    if face_system.start_continuous_recognition():
        print("Face Recognition System started successfully!")
        print("Press Ctrl+C to stop")
        
        try:
            while True:
                time.sleep(60)  # Print stats every minute
                stats = face_system.get_session_stats()
                if stats:
                    print(f"\nSession Stats:")
                    print(f"  Total detections: {stats['total_detections']}")
                    print(f"  Unique students: {stats['unique_students_detected']}")
                    print(f"  Confirmed attendance: {stats['confirmed_attendance']}")
                    
        except KeyboardInterrupt:
            print("\nShutting down...")
            face_system.stop_recognition()
    else:
        print("Failed to start face recognition system")

if __name__ == "__main__":
    main()