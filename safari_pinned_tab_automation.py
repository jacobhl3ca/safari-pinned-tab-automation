#!/usr/bin/env python3
"""
FAST Safari Pinned Tabs Automation

This is a Python automation script for macOS that efficiently manages Safari pinned tabs.
Supports both unpinning and closing operations with bidirectional navigation.
"""

import time
import pyautogui

# Safety settings
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1  # Reduced pause for faster execution

def get_user_options():
    print("=== Safari Pinned Tab Options ===")
    print()
    
    # Get operation type
    while True:
        operation = input("Choose operation: (1) Unpin tab (2) Close tab: ").strip()
        if operation in ['1', '2']:
            operation_type = 'unpin' if operation == '1' else 'close'
            break
        print("Please enter 1 or 2")
    
    # Get direction
    while True:
        direction = input("Choose direction: (1) <-- Right to left (2) --> Left to right: ").strip()
        if direction in ['1', '2']:
            direction_type = 'right_to_left' if direction == '1' else 'left_to_right'
            break
        print("Please enter 1 or 2")
    
    print()
    print(f"Operation: {operation_type.replace('_', ' ').title()}")
    print(f"Direction: {direction_type.replace('_', ' ').title()}")
    print()
    print("Position your mouse where you want to start")
    print()
    print("To stop the automation at any time, press Ctrl+C in this terminal, or move your mouse to the top left of your screen")
    print()
    
    return operation_type, direction_type

def main():
    print()
    print("Starting FAST Safari Pinned Tabs Automation...")
    print()
    
    # Get user options
    operation_type, direction_type = get_user_options()
    
    # Wait for user to be ready
    input("Press ENTER when you're ready to start (after positioning your mouse)...")
    
    # Short countdown
    print("Starting in 2...")
    time.sleep(1)
    print("1...")
    time.sleep(1)
    
    if operation_type == 'close':
        print("*** CLOSING PINNED TABS ***")
    else:  # unpin
        print("*** UNPINNING TABS ***")
    
    cycle = 0
    tab_distance = 36  # Distance between Safari pinned tabs
    
    try:
        while True:
            cycle += 1
            print(f"\n--- Cycle {cycle} ---")
            
            # Get current position
            current_x, current_y = pyautogui.position()
            print(f"Position: ({current_x}, {current_y})")
            
            # 1. Left click
            print("Clicking...")
            pyautogui.click()
            time.sleep(0.1)
            
            # 2. Right click
            print("Right click...")
            pyautogui.click(button='right')
            time.sleep(0.1)
            
            # 3. Press down arrow(s) - different for close vs unpin
            if operation_type == 'close':
                print("Down (3 times for close)...")
                pyautogui.press('down')
                time.sleep(0.05)
                pyautogui.press('down')
                time.sleep(0.05)
                pyautogui.press('down')
            else:  # unpin
                print("Down...")
                pyautogui.press('down')
            time.sleep(0.1)
            
            # 4. Press Enter
            print("Enter...")
            pyautogui.press('enter')
            time.sleep(0.1)
            
            # 5. Move based on direction and operation
            if direction_type == 'left_to_right':
                # Don't move mouse - both unpinning and closing shift tabs left automatically
                print("Not moving - tab action shifts remaining tabs left automatically")
            else:
                # Normal movement for right_to_left
                new_x = current_x - tab_distance
                print(f"Move left to ({new_x}, {current_y})")
                pyautogui.moveTo(new_x, current_y, duration=0.1)
            time.sleep(0.2)
            
            print(f"Cycle {cycle} done")
            
    except KeyboardInterrupt:
        print("\nStopped by user")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
