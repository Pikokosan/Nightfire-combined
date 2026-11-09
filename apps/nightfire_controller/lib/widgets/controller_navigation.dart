import 'package:flutter/material.dart';

class ControllerNavigation extends StatelessWidget {
  const ControllerNavigation({
    super.key,
    required this.selectedIndex,
    required this.onSelected,
    required this.pages,
  });

  final int selectedIndex;
  final ValueChanged<int> onSelected;
  final List<Widget> pages;

  static const destinations = [
    ('Modules', Icons.grid_view_outlined, Icons.grid_view),
    (
      'Fire',
      Icons.local_fire_department_outlined,
      Icons.local_fire_department,
    ),
    ('Scripts', Icons.description_outlined, Icons.description),
    ('Radio', Icons.usb_outlined, Icons.usb),
    (
      'Interface',
      Icons.settings_input_component_outlined,
      Icons.settings_input_component,
    ),
    ('Firmware', Icons.system_update_outlined, Icons.system_update),
    ('Mesh', Icons.hub_outlined, Icons.hub),
    ('Settings', Icons.settings_outlined, Icons.settings),
  ];

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, constraints) {
      final content = IndexedStack(index: selectedIndex, children: pages);
      if (constraints.maxWidth >= 900) {
        return Row(
          children: [
            NavigationRail(
              selectedIndex: selectedIndex,
              onDestinationSelected: onSelected,
              labelType: NavigationRailLabelType.all,
              destinations: [
                for (final (label, icon, selectedIcon) in destinations)
                  NavigationRailDestination(
                    icon: Icon(icon),
                    selectedIcon: Icon(selectedIcon),
                    label: Text(label),
                  ),
              ],
            ),
            const VerticalDivider(width: 1),
            Expanded(child: content),
          ],
        );
      }
      return Column(
        children: [
          Expanded(child: content),
          NavigationBar(
            selectedIndex: selectedIndex,
            onDestinationSelected: onSelected,
            destinations: [
              for (final (label, icon, selectedIcon) in destinations)
                NavigationDestination(
                  icon: Icon(icon),
                  selectedIcon: Icon(selectedIcon),
                  label: label,
                ),
            ],
          ),
        ],
      );
    },
  );
}
