# state.py
# Global in-memory state for user login flows
# Used by handlers to keep track of each user's current step

user_states = {}  # user_id -> state identifier (e.g., 'initial', 'code_phone', 'logged', 'batch')
