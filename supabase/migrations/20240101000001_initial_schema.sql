-- Create verification_tests table for infrastructure verification
-- This table is used by the demo app to verify database connectivity and operations
CREATE TABLE IF NOT EXISTS public.verification_tests (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    message TEXT NOT NULL,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Enable Row Level Security
ALTER TABLE public.verification_tests ENABLE ROW LEVEL SECURITY;

-- Create policies for verification_tests table
CREATE POLICY "Users can view their own verification tests" ON public.verification_tests
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own verification tests" ON public.verification_tests
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete their own verification tests" ON public.verification_tests
    FOR DELETE USING (auth.uid() = user_id);

-- Create index for better performance
CREATE INDEX IF NOT EXISTS idx_verification_tests_user_id ON public.verification_tests(user_id);
CREATE INDEX IF NOT EXISTS idx_verification_tests_created_at ON public.verification_tests(created_at DESC);

-- Create function to automatically update updated_at timestamp (for future use)
CREATE OR REPLACE FUNCTION public.handle_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
