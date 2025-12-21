// Legacy types - kept for backward compatibility if needed
export interface Todo {
  id: string;
  title: string;
  completed: boolean;
  created_at: string;
  updated_at: string;
  user_id: string;
}

// Verification test types
export interface VerificationTest {
  id: string;
  message: string;
  user_id: string;
  created_at: string;
}
