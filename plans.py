class SubscriptionPlan:
    def __init__(self, plan_name, price, permissions):
        self.plan_name = plan_name
        self.price = price
        self.permissions = permissions

    def __repr__(self):
        return f"{self.plan_name} - ${self.price}, Permissions: {self.permissions}"


class PermissionMapper:
    def __init__(self):
        self.plans = []

    def add_plan(self, plan):
        self.plans.append(plan)

    def get_permissions(self, plan_name):
        for plan in self.plans:
            if plan.plan_name == plan_name:
                return plan.permissions
        return None

# Example usage
if __name__ == "__main__":
    basic_plan = SubscriptionPlan("Basic", 5.99, ['read'])
    premium_plan = SubscriptionPlan("Premium", 14.99, ['read', 'write', 'delete'])

    mapper = PermissionMapper()
    mapper.add_plan(basic_plan)
    mapper.add_plan(premium_plan)

    print(mapper.get_permissions("Premium"))  # Output: ['read', 'write', 'delete']